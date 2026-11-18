// A lane as a three-letter colour code (VLK, ROC, CUD, CPU, NPU, FLM, EXT). The full name is the
// tooltip and the accessible name; pass `title` to say more than the lane's own name.
import { LANE_CODES } from './lane-code-pure'

export function LaneCode({ lane, title, testid, ...rest }) {
  const meta = Object.hasOwn(LANE_CODES, lane) ? LANE_CODES[lane] : LANE_CODES.cpu
  const name = title || meta.label
  return (
    <span className={'lane-code lane-' + lane} role="img" aria-label={name} title={name} data-testid={testid} {...rest}>
      {meta.code}
    </span>
  )
}
