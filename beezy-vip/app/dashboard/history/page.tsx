// Dashboard history -- deferred until Clerk + paid access launch
// Previously used lib/db.ts direct pool; migrated to public API when built out

export default function DashboardHistoryPage() {
  return (
    <div style={{ padding: '40px 20px', textAlign: 'center' }}>
      <p style={{ color: 'var(--fog)', fontFamily: 'monospace', fontSize: '12px' }}>
        Full history available after launch.
      </p>
    </div>
  )
}
