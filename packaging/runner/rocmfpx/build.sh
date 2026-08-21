#!/usr/bin/env bash
# Reproduce the ROCmFPX runner image from tracked source (#1970).
#
# Everything variable lives in manifest.toml; this script only executes it.
# A clean checkout of this repo plus a build host with docker/podman and
# network is the entire input — which is the point. `ade07ba` was a
# hand-build, and isolating #1888 meant reconstructing its lineage from
# scratch; a signed default pin must never be in that position again.
#
#   ./build.sh                 # build the tag named in the manifest
#   ./build.sh --tag foo:bar   # build under a different tag (candidates)
#   ./build.sh --check         # verify the patch series applies, build nothing
#
# NOT run on a production box. Slot inference and image builds compete for the
# same GPU host; build on the dedicated build host.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${HERE}/manifest.toml"
WORK="${HAL0_RUNNER_BUILD_DIR:-/tmp/hal0-rocmfpx-build}"
TAG_OVERRIDE=""
CHECK_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) TAG_OVERRIDE="$2"; shift 2 ;;
        --check) CHECK_ONLY=1; shift ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done

# Read the manifest with the stdlib rather than a TOML CLI, so the only build
# dependency is a python3 that already ships on every supported host.
read_manifest() {
    python3 - "$MANIFEST" "$1" <<'PY'
import sys, tomllib
doc = tomllib.load(open(sys.argv[1], "rb"))
cur = doc
for part in sys.argv[2].split("."):
    cur = cur[part]
if isinstance(cur, list):
    print("\n".join(str(x) for x in cur))
else:
    print(cur)
PY
}

TAG="${TAG_OVERRIDE:-$(read_manifest image.tag)}"
BASE="$(read_manifest base.image)"
REPO="$(read_manifest source.repo)"
REF="$(read_manifest source.ref)"
mapfile -t CMAKE_FLAGS < <(read_manifest build.cmake_flags)
mapfile -t PATCHES < <(python3 - "$MANIFEST" <<'PY'
import sys, tomllib
doc = tomllib.load(open(sys.argv[1], "rb"))
for p in doc.get("patches", []):
    print(p["file"])
PY
)

RUNTIME="${HAL0_CONTAINER_RUNTIME:-$(command -v docker || command -v podman)}"
[[ -n "$RUNTIME" ]] || { echo "no docker/podman on PATH" >&2; exit 65; }

echo "==> tag    ${TAG}"
echo "==> base   ${BASE}"
echo "==> source ${REPO} @ ${REF}"
echo "==> patches ${#PATCHES[@]}"

SRC="${WORK}/src"
rm -rf "$SRC"; mkdir -p "$SRC"
# Check out the exact commit, never a branch tip: a branch would silently move
# and the resulting image would stop matching this manifest with no diff to
# show for it.
#
# Clone-then-checkout rather than `fetch --depth 1 <sha>`: fetching an
# arbitrary commit by SHA requires uploadpack.allowReachableSHA1InWant on the
# server, which is off by default and is NOT set on this source. A shallow
# fetch of the branch is no good either — the pinned commit may be older than
# any depth we guess. Correctness beats transfer size for a tool whose entire
# job is reproducing a signed artefact.
git clone -q --no-checkout "$REPO" "$SRC"
git -C "$SRC" checkout -q "$REF"
echo "==> checked out $(git -C "$SRC" rev-parse --short HEAD)"

for p in "${PATCHES[@]}"; do
    git -C "$SRC" apply --check "${HERE}/patches/${p}"
    git -C "$SRC" apply "${HERE}/patches/${p}"
    echo "==> applied ${p}"
done

if (( CHECK_ONLY )); then
    echo "==> --check: patch series applies cleanly against ${REF}; nothing built"
    exit 0
fi

BUILDER="localhost/hal0-rocmfpx-builder:$(read_manifest base.rocm_version)"
cat > "${WORK}/Containerfile.builder" <<EOF
FROM ${BASE}
RUN dnf install -y \
      rocm-llvm hipcc hip-devel rocm-device-libs \
      hipblas-devel rocblas-devel \
      gcc gcc-c++ cmake ninja-build make git \
      vulkan-headers vulkan-loader-devel glslc glslang spirv-headers-devel \
      libcurl-devel \
    && dnf clean all
ENV PATH=/opt/rocm/bin:\${PATH}
EOF
"$RUNTIME" build -f "${WORK}/Containerfile.builder" -t "$BUILDER" "$WORK"

"$RUNTIME" run --rm --entrypoint bash -v "${SRC}:/src" -w /src \
    -e JOBS="${JOBS:-6}" "$BUILDER" -c "
set -e
export HIPCXX=\"\$(hipconfig -l)/clang\"
export HIP_PATH=\"\$(hipconfig -R)\"
cmake -S . -B build $(printf '%s ' "${CMAKE_FLAGS[@]}")
cmake --build build -j \"\$JOBS\" --target llama-server llama-cli llama-bench llama-quantize
"

STAGE="${WORK}/stage"
rm -rf "$STAGE"; mkdir -p "$STAGE/bin"
cp -a "${SRC}"/build/bin/. "${STAGE}/bin/"
find "${SRC}/build" -name '*.so*' -exec cp -P {} "${STAGE}/bin/" \;
cp "${HERE}/entrypoint.sh" "${STAGE}/hal0-runner-entrypoint.sh"

cat > "${STAGE}/Containerfile" <<EOF
FROM ${BASE}
RUN dnf install -y mesa-vulkan-drivers vulkan-loader vulkan-tools && dnf clean all
COPY bin/ /opt/rocmfpx/bin/
COPY hal0-runner-entrypoint.sh /opt/rocmfpx/hal0-runner-entrypoint.sh
RUN chmod +x /opt/rocmfpx/hal0-runner-entrypoint.sh && \\
    rm -f /usr/local/bin/llama-* /usr/local/lib64/libllama* /usr/local/lib64/libggml* && \\
    for b in /opt/rocmfpx/bin/llama-*; do \\
      [ -f "\\\$b" ] && [ -x "\\\$b" ] && ln -sf "\\\$b" "/usr/local/bin/\\\$(basename "\\\$b")"; \\
    done && ldconfig || true
ENV LD_LIBRARY_PATH=/opt/rocmfpx/bin:/opt/rocm/lib \\
    HSA_OVERRIDE_GFX_VERSION=11.5.1 \\
    GGML_HIP_ENABLE_UNIFIED_MEMORY=1
# Provenance in the image itself, so a box can answer "where did this come
# from" without the repo (#1970).
LABEL org.opencontainers.image.source="${REPO}" \\
      org.opencontainers.image.revision="${REF}" \\
      dev.hal0.runner.recipe="packaging/runner/rocmfpx" \\
      dev.hal0.runner.base="${BASE}" \\
      dev.hal0.runner.patches="$(IFS=,; echo "${PATCHES[*]}")"
ENTRYPOINT ["/opt/rocmfpx/hal0-runner-entrypoint.sh"]
EOF
"$RUNTIME" build -f "${STAGE}/Containerfile" -t "$TAG" "$STAGE"
echo "==> built ${TAG}"
