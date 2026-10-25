// The Image Gen pane's Engine tab: the engine card (state, container, GPU gate, restart, logs, stop
// image mode), the editor card (workflow chips, the ComfyUI editor link), the queue card and the
// GPU device card.
//
// Everything is drawn from `GET /api/comfyui/status` and `/arbiter` as given. Memory is not on the
// tab: the old pane's GTT and RAM gauges are gone, and memory is the top bar ruler's and the fit
// meters'. The cards are EngineCard.jsx, EditorCard.jsx and EngineStatCards.jsx; the facts and the
// fixed words are engine-pure.ts.
//
// Mounted by ImageGenTabs inside the pane's `.comfy-page` wrapper (the `--comfy*` tokens). It
// stays mounted while another tab shows, so `visible` slows its /status poll to the idle cadence
// and keeps it from reading the log.
import './image-engine.css'
import { useArbiter, useComfyui } from '@/api/hooks/useComfyui'
import { EditorCard } from './EditorCard'
import { EngineCard } from './EngineCard'
import { DeviceCard, QueueCard } from './EngineStatCards'

/**
 * @param {object} props
 * @param {boolean} [props.visible]  this tab is the one showing
 */
export function ImageEngineTab({ visible = true }) {
  const status = useComfyui({ active: visible }).data
  const env = useArbiter().data
  return (
    <div className="ie" data-testid="engine-tab">
      <EngineCard status={status} env={env} visible={visible} />
      <EditorCard />
      <div className="ie-col">
        <QueueCard status={status} />
        <DeviceCard status={status} />
      </div>
    </div>
  )
}
