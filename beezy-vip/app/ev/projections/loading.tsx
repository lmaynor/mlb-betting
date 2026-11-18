import React from 'react'
import { ProjectionsCardsSkeleton } from '@/components/picks/projections-cards'

export default function ProjectionsLoading() {
  return (
    <div className="max-w-page mx-auto px-6 py-6">
      <ProjectionsCardsSkeleton />
    </div>
  )
}
