# Hermes-Agent env-discovery probe recipes (2026-05-23)

Reference material for the first-run bootstrap phase that runs as the
Hermes-Agent process on a hal0 host. The goal of this phase is to
build an `EnvironmentReport` dataclass that downstream installer
decisions can dispatch on (which model store path to use, whether to
enable NPU backends, whether to expect network egress, etc.).

Validated against the live hal0 LXC (`ssh hal0`, container 105 on
Proxmox `pve`) on 2026-05-23. Outputs shown are real snapshots from
that host so future maintainers can sanity-check changes.

All recipes prefer:

1. Reading sysfs/procfs — synchronous, no process spawn, no privilege.
2. `subprocess.run(["cmd", "--flag"], capture_output=True, text=True,
   timeout=5)` with `check=False` — never let a missing tool crash the
   probe.
3. Returning a dataclass field of the form `Optional[Detected[T]]`
   with three states: `Detected(value, source)`, `Unavailable(reason)`,
   `ProbeError(exc)`. Downstream code decides whether each is fatal.

Order matters: virtualization → CPU → RAM → GPU → NPU → network →
filesystem → tooling → checkpoint. Earlier results gate later probes
(e.g. NPU probe is skipped on a non-Strix host).

---

## 1. Containerization and virtualization layer

### 1.1 `systemd-detect-virt` is the cheapest oracle

```
$ systemd-detect-virt --container
lxc
$ systemd-detect-virt --vm
none
$ systemd-detect-virt
lxc
```

On the hal0-dev VM (KVM under Proxmox) the same commands return:

```
$ systemd-detect-virt --container
none
$ systemd-detect-virt --vm
kvm
$ systemd-detect-virt
kvm
```

Combined logic (Python):

```python
def detect_virt():
    container = run(["systemd-detect-virt", "--container"]).strip()
    vm = run(["systemd-detect-virt", "--vm"]).strip()
    if container != "none":
        return ("container", container, vm if vm != "none" else None)
    if vm != "none":
        return ("vm", vm, None)
    return ("bare-metal", None, None)
```

Expected mapping for hal0 deployments:

| Host                  | --container | --vm  | Resulting tuple                    |
|-----------------------|-------------|-------|------------------------------------|
| hal0 LXC (`105`)      | `lxc`       | none  | `("container", "lxc", None)`       |
| hal0-dev VM (`104`)   | `none`      | `kvm` | `("vm", "kvm", None)`              |
| Bare metal Strix box  | `none`      | `none`| `("bare-metal", None, None)`       |
| Docker run            | `docker`    | none  | `("container", "docker", None)`    |
| toolbx/distrobox      | `podman` /  `wsl` | varies | container=podman with extras  |

**Failure mode.** On a stripped-down image without systemd at all
(Alpine, scratch) `systemd-detect-virt` is missing — fall through to
the cgroup probe below.

### 1.2 Cross-check via `/proc/1/environ` and `/proc/1/cgroup`

The kernel-injected `container=` env var on PID 1 is the most reliable
LXC signal; it persists even on stripped images.

```
$ cat /proc/1/environ | tr '\0' '\n'
TERM=linux
container=lxc
container_ttys=pts/1 pts/2
```

Other expected `container=` values: `lxc`, `lxc-libvirt`, `systemd-nspawn`,
`docker`, `podman`, `oci`, `rkt`. `/run/systemd/container` mirrors this
when systemd is available (`cat /run/systemd/container` → `lxc`).

```
$ cat /proc/1/cgroup
0::/init.scope          # cgroup v2 unified
```

On older Docker engines you'll see `/docker/<id>` segments; on hal0 the
unified cgroup hides this, so don't rely on cgroup-path parsing as a
primary signal.

### 1.3 Docker / Podman / toolbx / distrobox markers

| Container kind     | Sentinel file                  | Detection                       |
|--------------------|--------------------------------|---------------------------------|
| Docker             | `/.dockerenv`                  | `Path("/.dockerenv").exists()`  |
| Podman             | `/run/.containerenv`           | also present in OCI containers  |
| toolbx             | `/run/.toolboxenv`             | toolbx-specific                 |
| distrobox          | `/run/.containerenv` + env `CONTAINER_ID` set, `/etc/host` mounts | check env `DISTROBOX_HOST_HOME` |
| systemd-nspawn     | env `container=systemd-nspawn` on PID 1                          | as above                        |

On hal0 LXC, none of these exist:

```
$ ls /.dockerenv /run/.containerenv /run/.toolboxenv 2>&1
ls: cannot access '/.dockerenv': No such file or directory
ls: cannot access '/run/.containerenv': No such file or directory
ls: cannot access '/run/.toolboxenv': No such file or directory
```

### 1.4 Proxmox guest vs bare metal

DMI fields work everywhere systemd-detect-virt does, plus give the
chassis-level brand name:

```
$ cat /sys/class/dmi/id/sys_vendor
Micro Computer (HK) Tech Limited
$ cat /sys/class/dmi/id/product_name
MS-S1 MAX
$ cat /sys/class/dmi/id/bios_vendor      # `American Megatrends` on real iron
$ cat /sys/class/dmi/id/board_vendor     # vendor of the motherboard
```

Inside a Proxmox KVM VM you see `QEMU` / `Standard PC (Q35 + ICH9, 2009)`.
Inside an LXC, the DMI fields **pass through from the host** — hal0 LXC
reports `MS-S1 MAX` because that's the bare-metal host (`pve`). That's
the asymmetry to remember: LXC sees host DMI; VM sees QEMU DMI.

So the combined classifier is:

```
LXC on bare metal  :  --container=lxc, DMI=real chassis
LXC on KVM         :  --container=lxc, DMI=QEMU      (uncommon)
KVM VM (Proxmox)   :  --vm=kvm,        DMI=QEMU
Bare metal Strix   :  --container=none, --vm=none,   DMI=real chassis
```

### 1.5 LXC privileged vs unprivileged

Read `/proc/self/uid_map`. Privileged LXCs map `0 0 4294967295`
(identity over the entire range). Unprivileged LXCs map `0 100000 65536`
or similar offset.

```
$ cat /proc/self/uid_map
         0          0 4294967295
```

That's privileged. As a backup check, `CapEff` in `/proc/self/status`
will be non-zero for privileged root (`000001fcfdfcffff` on hal0).

Apparmor state:

```
$ cat /proc/self/attr/current
unconfined
```

`unconfined` confirms `lxc.apparmor.profile: unconfined` in the LXC
config — required for ROCm / XDNA passthrough to work. Other values
to recognise: `lxc-container-default`, `lxc-container-default-cgns`,
or a named profile.

Failure modes:
- `aa-status` requires capability `CAP_MAC_ADMIN` (root + privileged
  container). Don't rely on it from the probe; just read
  `/proc/self/attr/current` and `/proc/self/attr/exec`.
- On a kernel built without apparmor, `/proc/self/attr/current` is
  empty or missing. Treat that as "no LSM" rather than `unconfined`.

---

## 2. CPU

### 2.1 Strix Halo identification

```
$ grep -m1 'model name' /proc/cpuinfo
model name      : AMD RYZEN AI MAX+ 395 w/ Radeon 8060S
$ grep -m1 'vendor_id' /proc/cpuinfo
vendor_id       : AuthenticAMD
```

The exact model string for Strix Halo SKUs in the wild:

| SKU                                 | model name regex                                |
|-------------------------------------|-------------------------------------------------|
| Ryzen AI Max+ 395                   | `RYZEN AI MAX\+ 395`                            |
| Ryzen AI Max 390                    | `RYZEN AI MAX 390`                              |
| Ryzen AI Max 385                    | `RYZEN AI MAX 385`                              |
| Older Phoenix (XDNA1)               | `Ryzen 7 7840U` / `Ryzen 7 7840HS`              |

Treat any `RYZEN AI MAX` match as Strix Halo (XDNA2 + gfx1151). Be
defensive: AMD has renamed mobile chips three times in 18 months;
the *board* name is the safer pin (see `/sys/class/dmi/id/product_name`).

### 2.2 Core counts

```
$ nproc                     # logical (online) — 16 on hal0 LXC
$ lscpu -p=CORE | grep -v '^#' | sort -u | wc -l   # physical cores
12
$ lscpu | grep -E 'Socket|Core|Thread|CPU\('
CPU(s):                                  32
On-line CPU(s) list:                     0-2,5,7,8,16,17,19-23,25,28,30
Off-line CPU(s) list:                    3,4,6,9-15,18,24,26,27,29,31
Thread(s) per core:                      2
Core(s) per socket:                      16
Socket(s):                               1
```

Three different "core counts" to keep separate:

1. **Total topology** — `CPU(s):` (32 on a 16C/32T Strix Halo). This
   reflects what the kernel *knows about*, not what's usable.
2. **Online logical** — `nproc` (16 on hal0 LXC). Reflects cgroup
   `cpuset.cpus.effective` constraints. **Use this for thread-pool
   sizing.**
3. **Physical cores** — `lscpu -p=CORE | sort -u`. 12 on hal0 LXC.
   Useful for `--threads N` flags on llama.cpp that don't benefit
   from SMT siblings.

The "Off-line CPU(s) list" output on hal0 reflects deliberate
`cpuset` partitioning at the LXC level. Don't treat it as an error.

### 2.3 ISA extensions for inference

Parse the `flags:` line of `/proc/cpuinfo` once and bake into a set:

```python
flags = set(open("/proc/cpuinfo").read().split("flags\t\t:", 1)[1]
            .split("\n", 1)[0].split())
isa = {
    "avx2":     "avx2"     in flags,
    "avx512f":  "avx512f"  in flags,
    "avx512bw": "avx512bw" in flags,
    "avx512_vnni":     "avx512_vnni"     in flags,
    "avx512_bf16":     "avx512_bf16"     in flags,
    "avx_vnni":        "avx_vnni"        in flags,
    "amx_tile": "amx_tile" in flags,   # Intel Sapphire Rapids+ only
    "amx_bf16": "amx_bf16" in flags,
    "amx_int8": "amx_int8" in flags,
    "f16c":     "f16c"     in flags,
    "fma":      "fma"      in flags,
}
```

Strix Halo (snapshot from hal0):

```
avx avx2 avx512_bf16 avx512_bitalg avx512_vbmi2 avx512_vnni
avx512_vp2intersect avx512_vpopcntdq avx512bw avx512cd avx512dq
avx512f avx512ifma avx512vbmi avx512vl avx_vnni sse4_1 sse4_2 sse4a
```

So Strix Halo has the *full* AVX-512 set including VNNI + BF16. AMX is
Intel-only; absence is expected on AMD.

---

## 3. System RAM

### 3.1 Headline numbers

```
$ grep -E '^(MemTotal|MemAvailable|MemFree|SwapTotal)' /proc/meminfo
MemTotal:       98304000 kB
MemFree:        88978000 kB
MemAvailable:   93094233 kB
SwapTotal:       8388604 kB
```

`MemAvailable` (kernel-computed "what could be allocated without
swapping") is the right field for sizing decisions. `MemFree` is a
trap — it excludes reclaimable page cache.

In a Strix Halo system with 96 GiB of unified memory, expect
`MemTotal` around 98300000 kB (96 GiB minus a small reserved chunk
for GPU framebuffer). hal0 LXC reads 93 GiB (`free -h` line "Mem:
93Gi") — the delta from 96 GiB is the VRAM/GTT carve-out plus kernel
reserves.

### 3.2 cgroup limits inside LXC

Even when MemTotal reports the host's full RAM, the container can be
cgroup-capped. Check before trusting it:

```
$ cat /sys/fs/cgroup/memory.max
max
$ cat /sys/fs/cgroup/memory.current
9550794752
```

`max` = uncapped. Numeric value = byte ceiling — must be respected
when sizing model loads. Fall back to MemTotal only when memory.max
is `max` or missing.

### 3.3 Unified memory reporting

On Strix Halo the iGPU and NPU draw from the same physical pool as
the OS. The probe should:

- Report `ram_total_bytes` and `ram_available_bytes` from
  `/proc/meminfo`.
- Mark `memory_topology = "unified"` when:
  - the CPU model matches Strix Halo (§2.1), or
  - `/sys/class/drm/card*/device/mem_info_vram_total` returns a value
    well below the system RAM ceiling (1 GiB on hal0 — that's the
    UMA VRAM carve-out, not real dedicated VRAM).
- Otherwise mark `memory_topology = "discrete"`.

This matters because callers should size their model budget against
RAM, **not** against VRAM, on unified hosts. Discrete-VRAM logic
(NVIDIA, dGPU) needs the opposite.

---

## 4. iGPU (Radeon 8060S, gfx1151)

### 4.1 PCI enumeration

```
$ lspci -nn | grep -iE 'vga|display|3d'
bf:00.0 Display controller [0380]: Advanced Micro Devices, Inc. [AMD/ATI] Device [1002:1586] (rev c1)
```

PCI ID `1002:1586` is the Strix Halo iGPU (Radeon 8060S). Other AMD
IDs to recognise:

| PCI ID    | GPU                                | gfx target |
|-----------|------------------------------------|------------|
| 1002:1586 | Radeon 8060S (Strix Halo)          | gfx1151    |
| 1002:15bf | Radeon 780M (Phoenix)              | gfx1103    |
| 1002:1900 | Radeon 8050S (lesser Strix Halo)   | gfx1151    |

`lspci` can fail with `Unable to load libkmod resources: error -2`
inside an LXC — that's noise, the data still comes through. Don't
match on stderr.

### 4.2 amdgpu driver via `/sys/class/drm`

```
$ ls /sys/class/drm/
card1  card1-DP-1  ...  renderD128  version
$ cat /sys/class/drm/card1/device/uevent
DRIVER=amdgpu
PCI_CLASS=38000
PCI_ID=1002:1586
PCI_SUBSYS_ID=1F4C:B026
PCI_SLOT_NAME=0000:bf:00.0
```

The `DRIVER=amdgpu` field confirms the kernel driver loaded. If you
see `DRIVER=` empty (or no `card*` entry at all), amdgpu didn't bind —
that's the first failure to surface.

### 4.3 gfx target version (gfx1151 confirmation)

The most reliable gfx target probe reads the KFD topology:

```
$ cat /sys/class/kfd/kfd/topology/nodes/1/properties | grep -E 'gfx_target_version|device_id|simd_count'
simd_count 80
gfx_target_version 110501
device_id 5510
```

Decode `gfx_target_version`:

| Value   | gfx string |
|---------|------------|
| 110501  | gfx1151    |
| 110300  | gfx1103    |
| 110000  | gfx1100    |
| 90400   | gfx940     |
| 90a00   | gfx90a     |

Parse: `f"gfx{value // 10000}{(value // 100) % 100:02d}{value % 100:02d}"`
on hal0 gives `gfx1151`. Use this as the canonical identifier — it's
what ROCm/HSA queries return and what llama.cpp/Lemonade match on.

If `/sys/class/kfd/kfd/topology/nodes` is missing, AMDKFD didn't load
(common in stripped containers without `--device=/dev/kfd`).

### 4.4 VRAM budget (the dynamic UMA trap)

```
$ cat /sys/class/drm/card1/device/mem_info_vram_total
1073741824                              # 1 GiB — the UMA carve-out
$ cat /sys/class/drm/card1/device/mem_info_vram_used
477827072
$ cat /sys/class/drm/card1/device/mem_info_gtt_total
112742891520                            # ~105 GiB GTT pool
```

**Important nuance.** On Strix Halo `mem_info_vram_total` reports
**only the small dedicated carve-out** (BIOS-allocated UMA reserve).
The real budget the iGPU can use is `mem_info_gtt_total` plus
`mem_info_vram_total` — and even that is a soft ceiling pulled from
system RAM at allocation time.

Recipe for "effective VRAM budget":

```python
def vram_budget_bytes(card="card1"):
    vram = read_int(f"/sys/class/drm/{card}/device/mem_info_vram_total")
    gtt  = read_int(f"/sys/class/drm/{card}/device/mem_info_gtt_total")
    if vram + gtt > 16 * 1024**3:
        # UMA host — budget is RAM-bound, not VRAM-bound
        return ("unified", read_int("/proc/meminfo MemAvailable") * 1024)
    return ("dedicated", vram)
```

Surface both numbers in the report — downstream code that hard-codes
"if vram < model_size: refuse" will be wrong on Strix without that
distinction.

### 4.5 Vulkan availability

```
$ which vulkaninfo
$ vulkaninfo --summary 2>&1 | head
```

On hal0 LXC `vulkaninfo` is **not installed** (`command not found`).
That's a hal0-toolbox concern — the inference services bring their
own Vulkan loader. The host probe should treat missing `vulkaninfo` as
"can't confirm Vulkan from outside the toolbox; check from inside
the relevant toolbox image instead" rather than as a fatal flag.

If installed, parse:

```
GPU0:
        deviceName  = AMD Radeon Graphics (RADV GFX1151)
        apiVersion  = 1.3.296
        driverName  = radv
        deviceType  = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
```

### 4.6 ROCm availability

```
$ which rocm-smi
$ rocm-smi --showproductname
```

Missing on hal0 LXC (`command not found`). Same logic as
`vulkaninfo`: rocm-smi lives inside the ROCm toolbox image. Don't
treat absence as failure.

If you do need a CPU-side ROCm probe, check `/opt/rocm-*/bin/rocm-smi`
glob and `/dev/kfd` presence:

```
$ ls /dev/kfd
```

`/dev/kfd` is the ROCm userspace queue device. Present + readable =
amdkfd loaded = ROCm compute ready.

---

## 5. NPU (AMD XDNA2)

### 5.1 Device node

```
$ ls -la /dev/accel/
total 0
drwxr-xr-x  2 root root       60 May 18 02:56 .
drwxr-xr-x 10 root root      600 May 18 02:56 ..
crw-rw----  1 root render 261, 0 May 18 02:56 accel0
```

Major 261 is the accel chardev class. Presence of `/dev/accel/accel0`
is necessary and (combined with the driver check below) sufficient
for "NPU is usable".

For an unprivileged LXC, this is the recipe that *must* succeed:
the `dev0–dev3` cgroup passthrough and a privileged container with
apparmor unconfined are prerequisites (see haloai 220 LXC config
recipe, mirrored on hal0 105). If accel0 is missing, the bootstrap
should fail fast with a pointer to the LXC config — not silently
fall back to CPU NPU emulation (there is none).

### 5.2 Kernel driver binding

```
$ lsmod | grep -i xdna
amdxdna               159744  3
amd_pmf               106496  1 amdxdna
gpu_sched              69632  2 amdxdna,amdgpu
$ ls /sys/bus/pci/drivers/amdxdna/
0000:c0:00.1  bind  module  new_id  remove_id  uevent  unbind
$ cat /sys/bus/pci/drivers/amdxdna/0000:c0:00.1/uevent
DRIVER=amdxdna
PCI_CLASS=118000
PCI_ID=1022:17F0
PCI_SUBSYS_ID=1022:17F0
PCI_SLOT_NAME=0000:c0:00.1
```

PCI ID `1022:17F0` is XDNA2 (Strix Halo). Older XDNA1 (Phoenix) is
`1022:1502`. Use the PCI ID — not the model — to distinguish XDNA1
from XDNA2.

### 5.3 XRT tooling (optional)

```
$ which xrt-smi
$ xrt-smi examine
```

`xrt-smi` is not on hal0 LXC by default — the FLM toolbox bundles its
own XRT runtime. Probe should treat missing `xrt-smi` as "can't
deep-inspect from outside FLM toolbox" rather than as a failure.

When present, `xrt-smi examine` returns one of:

```
System Configuration
  OS Name              : Linux
  ...
Devices present
  [0000:c0:00.1] : NPU Strix          # XDNA2 confirmed
```

### 5.4 Firmware path

`/lib/firmware/amdnpu/` is **not** present on hal0 LXC (firmware lives
on the Proxmox host, loaded at kernel boot before the LXC starts).
Missing path inside the LXC ≠ missing firmware. Don't gate on it.

### 5.5 Driver version

```
$ modinfo amdxdna 2>&1 | head -5
modinfo: ERROR: Module amdxdna not found.
```

`modinfo` fails inside the LXC even when the module is loaded,
because module files live on the host. Use `lsmod | grep amdxdna`
(works) as the binary "loaded" check, and read `dmesg | grep -i
amdxdna` from the *host* (not the container) for version info if
needed. dmesg inside the LXC is empty for amdxdna lines.

### 5.6 Fail-fast policy

```python
def npu_status(env):
    if env.cpu_model_strix_halo is False:
        return Unavailable("non-Strix CPU; XDNA2 not expected")
    if not Path("/dev/accel/accel0").exists():
        if env.virt == ("container", "lxc"):
            return Fatal(
                "/dev/accel/accel0 missing inside LXC — add dev0..dev3 "
                "cgroup passthrough to the LXC config (see hal0 "
                "passthrough recipe) and reboot the container."
            )
        return Fatal("/dev/accel/accel0 missing; amdxdna driver not bound")
    if "amdxdna" not in run(["lsmod"]):
        return Fatal("amdxdna kernel module not loaded on host")
    return Detected({"pci": "1022:17F0", "xdna_gen": 2})
```

The hal0-installer convention: Fatal blocks the bootstrap on Strix
hosts (NPU is part of the value prop), Warn-but-continue on non-Strix.

---

## 6. Network reachability

### 6.1 Default route + local subnet

```
$ ip -j route
[{"dst":"default","gateway":"10.0.1.1","dev":"eth0",...},
 {"dst":"10.0.1.0/24","dev":"eth0","prefsrc":"10.0.1.142",...},
 {"dst":"172.17.0.0/16","dev":"docker0","prefsrc":"172.17.0.1",...}]
```

`ip -j` returns parseable JSON — always prefer over textual output.
Pick the route with `dst == "default"` for the gateway; pick the
non-loopback route whose `prefsrc` matches one of the host's IPv4
addresses for the local subnet.

### 6.2 Private vs public LAN classification

```python
import ipaddress
def is_private(ip):
    return ipaddress.ip_address(ip).is_private
```

`10.0.1.142` → True. Mark the host as `network_zone = "private"`. If
the host's primary IP is *public*, the bootstrap should warn before
binding services on 0.0.0.0 — the hal0 default assumes a private LAN
with no auth.

### 6.3 DNS resolvability

```
$ getent hosts hal0.thinmint.dev
10.0.1.142      hal0.thinmint.dev hal0
$ getent hosts releases.hal0.dev
172.67.213.81   releases.hal0.dev
104.21.86.5     releases.hal0.dev
```

`getent hosts` uses NSS — so it respects `/etc/hosts` overrides plus
mDNS plus DNS. Better than raw `dig` for bootstrap purposes because
it tells you what the running code will actually see.

Recipe:

```python
def resolves(name, timeout=2.0):
    try:
        return bool(subprocess.run(
            ["getent", "hosts", name],
            capture_output=True, timeout=timeout, check=False
        ).stdout.strip())
    except subprocess.TimeoutExpired:
        return False
```

Treat `hal0.local` resolvability as a soft signal — mDNS works on the
LAN, may not work inside Docker. Treat `releases.hal0.dev` as the
internet-egress probe.

### 6.4 Reachability without external libraries

Pure stdlib TCP poke:

```python
import socket
def tcp_open(host, port, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False
```

Test order:

| Target                       | Why                                           |
|------------------------------|-----------------------------------------------|
| `127.0.0.1:8081`             | hal0 primary slot (llama-server / lemonade)   |
| `127.0.0.1:8095`             | hal0-admin / haloai MCP gateway               |
| `10.0.1.220:8095`            | haloai LXC MCP (federated memory)             |
| `10.0.1.220:9130`            | Hermes router                                 |
| `releases.hal0.dev:443`      | update channel egress                         |

On hal0 LXC, `127.0.0.1:8095` is `Connection refused` (no local MCP
yet — that comes with v0.3). Treat refusal as "service not yet
started" rather than as a network failure.

### 6.5 Don't shell out to `ping`

`ping` requires CAP_NET_RAW in unprivileged contexts and is blocked
in many container default profiles. Use the TCP poke above instead.

---

## 7. Filesystem

### 7.1 Model store discovery

```
$ findmnt -nt zfs,ext4,nfs,nfs4,btrfs,xfs -o TARGET,SOURCE,FSTYPE
/                              /dev/mapper/pve-vm--105--disk--0 ext4
|-/var/lib/hal0/comfyui/models devpool/ai-models[/comfyui]      zfs
|-/mnt/ai-models               devpool/ai-models                zfs
|-/mnt/lab                     devpool/dev/lab                  zfs
|-/mnt/repos                   devpool/dev/repos                zfs
|-/mnt/projects                devpool/dev/projects             zfs
|-/mnt/artifacts               devpool/dev/artifacts            zfs
|-/mnt/share                   devpool/dev[/share]              zfs
```

`findmnt -J` (JSON) is the structured form; parse from there. For each
candidate model-store path (`/mnt/ai-models`, `/mnt/dock-models`,
`~/.cache/hal0/models`):

```python
def probe_fs(path):
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "exists": False}
    st = os.statvfs(p)
    fstype = run(["stat", "-f", "-c", "%T", str(p)]).strip()
    return {
        "path": str(p),
        "exists": True,
        "fstype": fstype,                            # zfs, ext4, nfs4, …
        "writable": os.access(p, os.W_OK),
        "free_bytes": st.f_bavail * st.f_frsize,
        "total_bytes": st.f_blocks * st.f_frsize,
    }
```

Expected hal0 LXC result for `/mnt/ai-models`:

```
{"path": "/mnt/ai-models", "exists": True, "fstype": "zfs",
 "writable": True, "free_bytes": ~511GB, "total_bytes": ~978GB}
```

(Past confusion: hal0 LXC's `/mnt/ai-models` is **rw ZFS** from devpool,
not the ro NFS export from `pve`'s `/mnt/ai-models`. See memory
`hal0_model_store_layout` — don't reintroduce the NFS assumption.)

### 7.2 NFS sniffing

```
$ findmnt -t nfs,nfs4 -J
```

Returns empty on hal0 LXC (no NFS mounts; everything is ZFS-direct
from the host). On the hal0-dev VM you'd see the dock-models NFS
share from `pve`. Use this to tell the two hosts apart programmatically.

### 7.3 Don't use `df` for programmatic data

`df` output is locale-dependent and column widths vary. Use
`os.statvfs` for free/total bytes; use `findmnt -J` for mount-tree
structure. Reserve `df -T` for human-readable debug.

---

## 8. Userland tooling

### 8.1 Existence check via `shutil.which`

```python
import shutil
def has(cmd): return shutil.which(cmd) is not None
```

Don't `subprocess.run([cmd, "--version"])` blindly — some binaries
(`flm` for instance) hang waiting for stdin if no TTY is attached.
Always pass `stdin=subprocess.DEVNULL` and `timeout=`.

Snapshot from hal0 LXC:

| Binary               | Present | Notes                                       |
|----------------------|---------|---------------------------------------------|
| `docker`             | yes     | Docker 29.1.3, daemon up                    |
| `podman`             | no      |                                             |
| `lemonade-server`    | no      | v0.2 migration in-flight, not yet installed |
| `llama-server`       | no      | inside hal0-toolbox-* images, not on host   |
| `flm`                | yes     | v0.9.42 — installed via PPA + .deb          |
| `python3`            | yes     | 3.12.3                                      |
| `uv`                 | depends |                                             |

### 8.2 Daemon health (Docker, Podman)

```python
def docker_running():
    r = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    return r.returncode == 0 and r.stdout.strip()
```

`docker info` returns non-zero if the daemon socket isn't reachable.
Don't `ps | grep dockerd` — both noisy and racy.

### 8.3 Python provenance

```python
import sys, sysconfig
report["python"] = {
    "version": sys.version.split()[0],     # "3.12.3"
    "executable": sys.executable,
    "prefix": sysconfig.get_paths()["data"],
    "uv": shutil.which("uv"),
    "pip": shutil.which("pip"),
}
```

For Hermes-Agent itself, prefer pinning to `sys.executable` rather
than `which python3` — the bootstrap should run in its own venv and
inherit that, not re-resolve.

### 8.4 FLM probe (Strix-specific)

```python
def flm_probe():
    if not shutil.which("flm"):
        return Unavailable("flm not on PATH")
    r = subprocess.run(
        ["flm", "list", "-j"],
        capture_output=True, text=True, timeout=10,
        stdin=subprocess.DEVNULL, check=False,
    )
    if r.returncode != 0:
        return ProbeError(f"flm list -j: {r.stderr.strip()}")
    return Detected(json.loads(r.stdout))
```

The `-j` flag is the only stable JSON contract on `flm`; the text
output reformats across releases.

---

## 9. First-run safety

### 9.1 Idempotent checkpoint file

The hal0 installer already uses `/var/lib/hal0/.first_run_done` and
`/var/lib/hal0/.first-run.lock` (confirmed on hal0 LXC). Hermes-Agent
bootstrap should adopt the same convention:

```
$XDG_DATA_HOME/hermes-agent/first_run_done           # success marker
$XDG_DATA_HOME/hermes-agent/first_run.lock           # held during run
$XDG_DATA_HOME/hermes-agent/env-report-<ISO8601>.json # per-run artefact
```

Resolution order for the base path:

1. `$HERMES_DATA_HOME` if set (explicit override).
2. `$XDG_DATA_HOME/hermes-agent` if `$XDG_DATA_HOME` is set.
3. `/var/lib/hermes-agent` if running as root and writable.
4. `~/.local/share/hermes-agent` otherwise.

Never write to `/tmp` for persistence — it's wiped on reboot, hiding
the "previous run failed" signal.

### 9.2 Detecting incomplete previous runs

```python
def previous_run_state(base: Path):
    done = base / "first_run_done"
    lock = base / "first_run.lock"
    if lock.exists() and not stale(lock):
        return "in-progress"     # another bootstrap is running NOW
    if lock.exists() and stale(lock):
        return "abandoned"       # offer to repair
    if done.exists():
        return "complete"
    return "fresh"
```

`stale()` rule of thumb: lock file mtime older than 1 hour and no
process with the PID listed inside it. Write `os.getpid()` into the
lock file at acquire time so you can check `/proc/<pid>` exists.

### 9.3 Atomic checkpoint write

```python
def mark_done(base: Path, report: dict):
    tmp = base / f".first_run_done.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(report, indent=2))
    tmp.rename(base / "first_run_done")  # atomic on POSIX
```

The `done` file should contain the full `EnvironmentReport` JSON so a
later session can read it without re-probing. On any version bump of
the schema, write `schema_version` into the file and check it before
trusting cached data.

### 9.4 Bootstrap log location

Single rotating file at `$base/bootstrap.log`. Rotate on bootstrap
start (move existing to `bootstrap.log.1`). Don't journald-tee — the
LXC + bare-metal + Docker cases all have different journald stories,
and a plain text file works everywhere.

Format: one line per probe, JSON-per-line so it's grep-able and
machine-parseable:

```
{"ts":"2026-05-23T12:00:01Z","probe":"virt","result":"container/lxc","ok":true}
{"ts":"2026-05-23T12:00:01Z","probe":"cpu","result":"strix-halo 16C/32T","ok":true}
{"ts":"2026-05-23T12:00:02Z","probe":"npu","result":"xdna2 1022:17F0","ok":true}
```

### 9.5 What to do on partial failure

Three severity levels:

- **Fatal** — block the bootstrap, print actionable error, exit 1.
  Reserved for: missing CPU model entirely (kernel weirdness), no
  writable data dir, no model store path resolvable.
- **Capability-disabled** — write the probe failure into the report,
  set the corresponding capability to `unavailable`, continue.
  Default for: NPU missing on non-Strix, ROCm tooling missing, Vulkan
  loader missing, Docker daemon down.
- **Warn-and-continue** — log a warning, proceed with reduced
  confidence. Default for: network egress blocked, mDNS not
  resolving, optional binaries missing.

The mapping is encoded in the probe table itself, not in ad-hoc
`if/else` at each call site — so policy changes in one place.

**NPU exception on hal0.** On a *Strix Halo LXC* with `/dev/accel/accel0`
missing, treat as Fatal — the hal0 product positions NPU as a
first-class capability, and silently CPU-only-fallback hides the
deployment misconfiguration. The error should point at the cgroup
passthrough recipe.

---

## Appendix: minimal probe-runner skeleton

```python
@dataclass
class Probe:
    name: str
    severity: Literal["fatal", "capability", "warn"]
    run: Callable[[], ProbeResult]

PROBES = [
    Probe("virt",       "fatal",      probe_virt),
    Probe("cpu",        "fatal",      probe_cpu),
    Probe("ram",        "fatal",      probe_ram),
    Probe("gpu",        "capability", probe_gpu),
    Probe("npu",        "capability", probe_npu),   # fatal on Strix
    Probe("network",    "warn",       probe_network),
    Probe("filesystem", "fatal",      probe_filesystem),
    Probe("tooling",    "warn",       probe_tooling),
]

def bootstrap():
    base = resolve_data_home()
    base.mkdir(parents=True, exist_ok=True)
    with acquire_lock(base):
        report = {"schema_version": 1, "ts": utcnow_iso()}
        for probe in PROBES:
            try:
                report[probe.name] = probe.run().to_dict()
            except FatalProbe as e:
                if probe.severity == "fatal":
                    raise
                report[probe.name] = {"error": str(e)}
        mark_done(base, report)
        return report
```

---

## See also

- `/etc/hal0/capabilities.toml` — capability rollup that consumes
  these probe results in the live hal0 installer.
- ADR-0006 (Lemonade migration) — defines which capabilities the
  v0.2 installer must report on.
- Memory: `strix-halo-lxc-passthrough` — privileged + apparmor
  unconfined + dev0–dev3 cgroup recipe that makes the NPU probe pass.
- Memory: `hal0_model_store_layout` — why `/mnt/ai-models` on hal0 LXC
  is rw ZFS and not the ro NFS export from `pve`.
