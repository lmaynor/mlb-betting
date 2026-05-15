import { NextResponse } from 'next/server'
import { generateAllArticles } from '@/lib/article-generator'
import { revalidatePath } from 'next/cache'

// Vercel cron: runs every Sunday at 03:00 UTC
// Set in vercel.json: { "crons": [{ "path": "/api/cron/refresh-articles", "schedule": "0 3 * * 0" }] }

export async function GET(req: Request) {
  // Vercel cron auth
  const authHeader = req.headers.get('authorization')
  if (authHeader !== `Bearer ${process.env.CRON_SECRET}`) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  try {
    const result = await generateAllArticles()
    revalidatePath('/learn', 'layout')

    console.log(`[cron] Article refresh: ${result.success.length} succeeded, ${result.failed.length} failed`)

    return NextResponse.json({
      success: result.success.length,
      failed:  result.failed.length,
      errors:  result.failed,
    })
  } catch (err) {
    console.error('[cron] Article refresh failed:', err)
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}
