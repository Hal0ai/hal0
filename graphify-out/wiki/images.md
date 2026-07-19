# images

> 46 nodes · cohesion 0.07

## Key Concepts

- **images()** (15 connections) — `src/hal0/providers/podman_introspect.py`
- **test_system_info_route.py** (12 connections) — `tests/api/test_system_info_route.py`
- **test_podman_introspect.py** (11 connections) — `tests/providers/test_podman_introspect.py`
- **PodmanImagesResult** (10 connections) — `src/hal0/providers/podman_introspect.py`
- **_backend_state()** (6 connections) — `src/hal0/api/routes/hardware.py`
- **podman_introspect.py** (6 connections) — `src/hal0/providers/podman_introspect.py`
- **TestClient** (6 connections)
- **_run()** (4 connections) — `src/hal0/providers/podman_introspect.py`
- **test_system_info_surfaces_rootful_podman_context()** (4 connections) — `tests/api/test_system_info_route.py`
- **test_system_info_surfaces_rootless_podman_context()** (4 connections) — `tests/api/test_system_info_route.py`
- **_recorder()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_falls_back_to_rootless_when_seam_denied()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_falls_back_when_seam_binary_missing_raises_oserror()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_routes_through_seam_when_hal0_user()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_skips_seam_when_not_hal0_user()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **_image_repo()** (3 connections) — `src/hal0/api/routes/hardware.py`
- **test_system_info_backends_expose_runner_supports_metadata()** (3 connections) — `tests/api/test_system_info_route.py`
- **test_images_returns_none_when_not_hal0_user_and_podman_missing()** (3 connections) — `tests/providers/test_podman_introspect.py`
- **_RunFn** (2 connections)
- **_parse_repos()** (2 connections) — `src/hal0/providers/podman_introspect.py`
- **_seam_argv()** (2 connections) — `src/hal0/providers/podman_introspect.py`
- **test_backend_state_installed_vs_installable()** (2 connections) — `tests/api/test_system_info_route.py`
- **test_backend_state_unavailable_when_podman_absent()** (2 connections) — `tests/api/test_system_info_route.py`
- **test_backends_degrade_to_unavailable_without_podman()** (2 connections) — `tests/api/test_system_info_route.py`
- **test_system_info_matches_hardware_and_features_endpoints()** (2 connections) — `tests/api/test_system_info_route.py`
- *... and 21 more nodes in this community*

## Relationships

- [hardware.py](hardware.py.md) (4 shared connections)

## Source Files

- `src/hal0/api/routes/hardware.py`
- `src/hal0/providers/podman_introspect.py`
- `tests/api/test_system_info_route.py`
- `tests/providers/test_podman_introspect.py`

## Audit Trail

- EXTRACTED: 114 (78%)
- INFERRED: 32 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*