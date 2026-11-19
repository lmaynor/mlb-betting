'use client'

import React from 'react'
import { SURFACE_CARD } from '@/lib/tokens'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import type { HeadshotSport } from '@/lib/sport-assets'

export interface CardShellProps {
  children?: React.ReactNode
  style?: React.CSSProperties
}

/**
 * Common card shell spreading SURFACE_CARD token with flex column layout.
 */
export function CardShell({ children, style }: CardShellProps) {
  return (
    <div
      style={{
        ...SURFACE_CARD,
        display: 'flex',
        flexDirection: 'column',
        ...style,
      }}
    >
      {children}
    </div>
  )
}

export interface CardHeaderProps {
  left?: React.ReactNode
  right?: React.ReactNode
  style?: React.CSSProperties
}

/**
 * Standard card header flex row rendering left and right slots.
 */
export function CardHeader({ left, right, style }: CardHeaderProps) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 8,
        flexWrap: 'wrap',
        ...style,
      }}
    >
      {left}
      {right}
    </div>
  )
}

export interface CardPlayerRowProps {
  sport: HeadshotSport | string
  headshotId?: number | string | null
  name: string
  sub?: React.ReactNode
  right?: React.ReactNode
  headshotSize?: number
  /** name font weight; 600 default, PropCard passes 700 to keep its shipped look */
  nameWeight?: number
  style?: React.CSSProperties
}

/**
 * Standard player flex row with PlayerHeadshot, a 15px semibold name block with sub ReactNode, and right slot.
 */
export function CardPlayerRow({
  sport,
  headshotId,
  name,
  sub,
  right,
  headshotSize = 40,
  nameWeight = 600,
  style,
}: CardPlayerRowProps) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 12,
        ...style,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <PlayerHeadshot
          sport={sport as HeadshotSport}
          id={headshotId}
          name={name}
          size={headshotSize}
        />
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: 15,
              fontWeight: nameWeight,
              color: '#f3f4f6',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              lineHeight: 1.25,
            }}
            title={name}
          >
            {name}
          </div>
          {sub}
        </div>
      </div>
      {right != null && (
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          {right}
        </div>
      )}
    </div>
  )
}
