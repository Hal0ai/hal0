// DIAGNOSTICS ▸ Doctor (D6).
//
// Was a labelled stub ("no HTTP endpoint with stable diagnosis IDs yet"). D6
// mounts the generic DiagnosisPanel here: `hal0 doctor` emits typed diagnoses
// (HAL0-* id / severity / evidence / next_steps — src/hal0/diagnostics.py) and
// GET /api/doctor serves them over HTTP, so the panel renders the SERVER's
// verdict rows directly (#1458). On a backend that predates the route the hook
// degrades to synthesised GET /api/system-info hardware evidence and the panel
// says so in a stub-with-reason — never a fabricated pass.
import { DiagnosisPanel } from '../../../diagnostics/DiagnosisPanel.jsx'

export function DoctorPage() {
  return <DiagnosisPanel />
}
