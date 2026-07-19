# images

> 40 nodes

## Key Concepts

- **images()** (15 connections) — `src/hal0/providers/podman_introspect.py`
- **test_system_info_route.py** (12 connections) — `tests/api/test_system_info_route.py`
- **test_podman_introspect.py** (11 connections) — `tests/providers/test_podman_introspect.py`
- **PodmanImagesResult** (10 connections) — `src/hal0/providers/podman_introspect.py`
- **podman_introspect.py** (6 connections) — `src/hal0/providers/podman_introspect.py`
- **TestClient** (6 connections)
- **_run()** (4 connections) — `src/hal0/providers/podman_introspect.py`
- **test_system_info_surfaces_rootful_podman_context()** (4 connections) — `tests/api/test_system_info_route.py`
- **test_system_info_surfaces_rootless_podman_context()** (4 connections) — `tests/api/test_system_info_route.py`
- **_recorder()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_skips_seam_when_not_hal0_user()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_routes_through_seam_when_hal0_user()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_falls_back_to_rootless_when_seam_denied()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_falls_back_when_seam_binary_missing_raises_oserror()** (4 connections) — `tests/providers/test_podman_introspect.py`
- **test_system_info_backends_expose_runner_supports_metadata()** (3 connections) — `tests/api/test_system_info_route.py`
- **test_images_returns_none_when_not_hal0_user_and_podman_missing()** (3 connections) — `tests/providers/test_podman_introspect.py`
- **_seam_argv()** (2 connections) — `src/hal0/providers/podman_introspect.py`
- **_RunFn** (2 connections)
- **_parse_repos()** (2 connections) — `src/hal0/providers/podman_introspect.py`
- **test_system_info_route_folds_hardware_features_backends()** (2 connections) — `tests/api/test_system_info_route.py`
- **test_system_info_matches_hardware_and_features_endpoints()** (2 connections) — `tests/api/test_system_info_route.py`
- **test_backends_degrade_to_unavailable_without_podman()** (2 connections) — `tests/api/test_system_info_route.py`
- **test_images_returns_none_when_seam_denied_and_podman_absent()** (2 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_returns_none_on_rootless_subprocess_error()** (2 connections) — `tests/providers/test_podman_introspect.py`
- **test_images_returns_none_on_rootless_nonzero_exit()** (2 connections) — `tests/providers/test_podman_introspect.py`
- *... and 15 more nodes in this community*

## Relationships

- [hardware.py](hardware.py.md) (3 shared connections)

## Source Files

- `src/hal0/providers/podman_introspect.py`
- `tests/api/test_system_info_route.py`
- `tests/providers/test_podman_introspect.py`

## Audit Trail

- EXTRACTED: 103 (79%)
- INFERRED: 28 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*