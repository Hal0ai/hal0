"""GPU display name — the one home of the short label for the probed GPU (#3156).

``GPUInfo.name`` is whatever the detector saw. On AMD that is an ``lspci``
line, and on a box whose pci.ids predates the part it reads
``Advanced Micro Devices, Inc. [AMD/ATI] Device 1586 (rev c1)`` — it tells the
operator nothing and truncates in a card caption. ``gpu_display_name`` derives
the label from that string; ``api/routes/hardware.py`` publishes it as
``gpu_display_name`` beside the raw ``gpu_name`` (diagnostics keep reading the
raw string), and the bench runner's host label calls it too. Nothing else
maps a PCI string to a name.

Pure: no sysfs, no subprocess.
"""

from __future__ import annotations

import re

# The vendor boilerplate ``lspci`` puts before every AMD part name. Its presence
# is also how an lspci line is told from a name that is already a label
# (``nvidia-smi`` product name, the probe's own "AMD GPU" placeholder).
_LSPCI_AMD_PREFIX = re.compile(
    r"^\s*Advanced Micro Devices,\s*Inc\.\s*\[AMD/ATI\]\s*", re.IGNORECASE
)
_REVISION_SUFFIX = re.compile(r"\s*\(rev\s+[0-9a-f]+\)\s*$", re.IGNORECASE)
# What lspci prints when pci.ids has no entry for the device id.
_UNRESOLVED_DEVICE = re.compile(r"\bDevice\s+[0-9a-f]{4}\b", re.IGNORECASE)
# The marketing name pci.ids brackets after the codename: ``Navi 31 [Radeon RX 7900 XTX]``.
_BRACKETED_RADEON = re.compile(r"\[([^\]]*Radeon[^\]]*)\]")
# pci.ids joins the SKUs of one die with " / " when the id alone cannot say which.
_SKU_SEPARATOR = " / "
# An APU's CPU string names its iGPU: ``AMD RYZEN AI MAX+ 395 w/ Radeon 8060S``.
# A bare "Radeon Graphics" names nothing, so a model number is required.
_CPU_RADEON = re.compile(r"\bRadeon\s+(?:Vega\s+)?\d+[A-Za-z]*\b")


def gpu_display_name(name: str | None, *, cpu_model: str = "") -> str:
    """Short operator-facing label for the probed GPU ``name``.

    * Not an ``lspci`` AMD line (NVIDIA, a VM's virtio GPU, an already-friendly
      name): returned as is.
    * ``lspci`` line pci.ids could not resolve (``Device 1586``): the iGPU
      marketing name from ``cpu_model`` when it carries one, else
      ``AMD Device 1586``.
    * ``lspci`` line with several SKUs bracketed (``Radeon Graphics / Radeon
      8050S / Radeon 8060S``): the one ``cpu_model`` names, else the bracket.
    * Any other resolved line: the bracketed Radeon marketing name, else the
      codename.

    ``cpu_model`` only supplies an iGPU's name. A resolved single-SKU card is
    never relabelled by the CPU it sits beside; an unresolved card on an APU box
    does read as the APU's iGPU, the limit of deriving a name from a CPU string.
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    stripped = _LSPCI_AMD_PREFIX.sub("", raw)
    if stripped == raw:
        return raw
    pci = _REVISION_SUFFIX.sub("", stripped).strip()
    match = _CPU_RADEON.search(cpu_model or "")
    igpu = str(match.group(0)) if match else ""
    if _UNRESOLVED_DEVICE.search(pci):
        return igpu or f"AMD {pci}"
    bracketed = _BRACKETED_RADEON.findall(pci)
    if not bracketed:
        return pci
    marketing = str(bracketed[-1]).strip()
    if igpu and _SKU_SEPARATOR in marketing:
        return igpu
    return marketing
