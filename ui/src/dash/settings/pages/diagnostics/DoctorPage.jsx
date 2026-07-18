// DIAGNOSTICS ▸ Doctor (D6, post-R3 surface rework).
//
// Was a labelled stub ("no HTTP endpoint with stable diagnosis IDs yet"). D6
// mounts the generic DiagnosisPanel here: `hal0 doctor` now emits typed
// diagnoses (HAL0-* id / severity / evidence / next_steps — src/hal0/
// diagnostics.py), and while there's still no /api/doctor HTTP feed, the panel
// wires to the real data that DOES exist (GET /api/system-info hardware
// evidence) and renders it in the exact generic shape the doctor feed will use.
// The missing verdict feed is shown in-panel as a stub-with-reason (API-lane
// request: GET /api/doctor), never faked.
import { DiagnosisPanel } from '../../../diagnostics/DiagnosisPanel.jsx'

export function DoctorPage() {
  return <DiagnosisPanel />
}
