"""test_control.py — worker/queue control state: safe defaults and the locked
read-modify-write cycles (the old lock-free RMW lost queue items under a
concurrent API enqueue + worker dequeue)."""

from __future__ import annotations

import threading

import pytest

from hal0.bench import control


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    return tmp_path


def test_control_defaults_to_stopped_and_exclusive():
    c = control.read_control()
    assert c == {"state": "stopped", "exclusive": True}
    assert not control.worker_should_run()


def test_set_control_validates_state():
    with pytest.raises(ValueError):
        control.set_control(state="bogus")
    control.set_control(state="running", exclusive=False)
    assert control.read_control() == {"state": "running", "exclusive": False}
    assert control.worker_should_run()


def test_enqueue_dequeue_roundtrip():
    control.enqueue({"id": "a", "model": "m1"})
    control.enqueue({"id": "b", "suite": "roster"})
    assert [i["id"] for i in control.read_queue()] == ["a", "b"]
    control.dequeue("a")
    assert [i["id"] for i in control.read_queue()] == ["b"]


def test_concurrent_enqueues_lose_nothing():
    """20 threads x 5 items each — every item must survive the RMW cycle."""

    def add(t: int) -> None:
        for i in range(5):
            control.enqueue({"id": f"{t}-{i}"})

    threads = [threading.Thread(target=add, args=(t,)) for t in range(20)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(control.read_queue()) == 100


def test_concurrent_enqueue_and_dequeue_lose_nothing():
    for i in range(50):
        control.enqueue({"id": f"pre-{i}"})

    def drain() -> None:
        for i in range(50):
            control.dequeue(f"pre-{i}")

    def add() -> None:
        for i in range(50):
            control.enqueue({"id": f"new-{i}"})

    t1, t2 = threading.Thread(target=drain), threading.Thread(target=add)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    ids = [i["id"] for i in control.read_queue()]
    assert sorted(ids) == sorted(f"new-{i}" for i in range(50))
