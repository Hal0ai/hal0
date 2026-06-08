// hal0 dashboard — Profiles view (issue #658).
//
// Lists container-slot profiles from GET /api/profiles.
// Each profile is a named image + bench-tuned flag bundle that backs
// one or more container slots. The UI labels them by intent (what they're
// for) rather than by slug, so operators see "MoE agents · ROCmFP4 ·
// ~52.8 tok/s" rather than "moe-rocmfp4".
//
// Intent labels + tok/s estimates come from bench data (not the API) for
// the three seed profiles. Unknown / custom profiles fall back to the
// image tag + mtp flag.

import { useProfiles } from '@/api/hooks/useProfiles'

// Seed profile intent labels, mapped by slug.
// Tok/s from hal0-container-bench-2026-06-08.md.
const PROFILE_INTENT = {
  'moe-rocmfp4':       'MoE agents · ROCmFP4 · ~52.8 tok/s',
  'dense-mtp-rocmfp4': 'Dense chat + MTP · ~24.4 tok/s',
  'vulkan-std':        'Vulkan std (fallback)',
};

function profileIntent(p) {
  if (PROFILE_INTENT[p.name]) return PROFILE_INTENT[p.name];
  // Custom profile: derive a label from image + mtp flag.
  const base = p.image ? p.image.split(':').pop() : p.name;
  return p.mtp ? `${base} · MTP` : base;
}

function imageTag(image) {
  if (!image) return '—';
  const parts = image.split(':');
  return parts.length > 1 ? parts[parts.length - 1] : image;
}

function ProfileCard({ profile }) {
  const intent = profileIntent(profile);
  const tag = imageTag(profile.image);
  return (
    <div className="pf-card">
      <div className="pf-intent">{intent}</div>
      <div className="pf-meta mono">
        <span className="pf-slug">{profile.name}</span>
        <span className="pf-sep">·</span>
        <span className="pf-tag">{tag}</span>
        {profile.mtp && <span className="pf-badge">MTP</span>}
      </div>
      {profile.resolved_flags && (
        <div className="pf-flags mono">{profile.resolved_flags}</div>
      )}
    </div>
  );
}

function ProfilesView() {
  const query = useProfiles();
  const profiles = query.data ?? [];

  if (query.isLoading) {
    return (
      <div className="view">
        <div className="view-head">
          <h2>Profiles</h2>
        </div>
        <div className="empty mono">Loading profiles…</div>
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="view">
        <div className="view-head">
          <h2>Profiles</h2>
        </div>
        <div className="empty mono" style={{color: 'var(--err)'}}>
          Failed to load profiles: {query.error?.message || 'unknown error'}
        </div>
      </div>
    );
  }

  return (
    <div className="view">
      <div className="view-head">
        <h2>Profiles</h2>
        <div className="view-sub mono">
          Container-slot templates — image + bench-tuned flags per inference workload.
        </div>
      </div>

      {profiles.length === 0 ? (
        <div className="empty mono">No profiles configured. Add profiles to /etc/hal0/profiles.toml.</div>
      ) : (
        <div className="pf-list">
          {profiles.map(p => (
            <ProfileCard key={p.name} profile={p} />
          ))}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { ProfilesView });
