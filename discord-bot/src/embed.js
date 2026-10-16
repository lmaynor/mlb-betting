// Dark-terminal embeds. Colors: emerald for +EV/wins, sky for projections,
// red for warnings/errors. ASCII only; no emoji.
import { ActionRowBuilder, ButtonBuilder, ButtonStyle, EmbedBuilder } from 'discord.js';
import {
  formatAmerican, formatNum, formatPct, formatPctPoints,
} from './beezyApi.js';

export const COLORS = {
  emerald: 0x57F287,
  sky: 0x38bdf8,
  red: 0xef4444,
  slate: 0x1f2937,
};

export function siteUrl() {
  return (process.env.SITE_URL || 'https://beezy.fyi').replace(/\/+$/, '');
}

const FOOTER = 'beezy.fyi | paper tracking, not advice';

function base(color, title) {
  return new EmbedBuilder().setColor(color).setTitle(title.slice(0, 256)).setFooter({ text: FOOTER }).setTimestamp();
}

/** Clamp a string for an embed description (4096) / field (1024). */
export function clip(text, max) {
  const s = String(text ?? '');
  return s.length <= max ? s : `${s.slice(0, max - 3)}...`;
}

/** Fixed-width table inside a code block. rows: array of arrays of strings. */
export function codeTable(headers, rows, maxChars = 1000) {
  const all = [headers, ...rows].map((r) => r.map((c) => String(c ?? '')));
  const widths = headers.map((_, i) => Math.max(...all.map((r) => (r[i] || '').length)));
  const lines = all.map((r) => r.map((c, i) => c.padEnd(widths[i])).join('  ').trimEnd());
  let body = lines.join('\n');
  if (body.length > maxChars) body = `${body.slice(0, maxChars - 4)}\n...`;
  return `\`\`\`\n${body}\n\`\`\``;
}

export function linkButtons(items) {
  const row = new ActionRowBuilder();
  for (const { label, url } of items) {
    row.addComponents(new ButtonBuilder().setLabel(label).setStyle(ButtonStyle.Link).setURL(url));
  }
  return row;
}

export function evButtons() {
  return linkButtons([
    { label: 'Open Terminal', url: `${siteUrl()}/ev` },
    { label: 'Inspect Cockpit', url: `${siteUrl()}/edge` },
  ]);
}

/** Plain error embed. Never include stack traces or secrets in `message`. */
export function errorEmbed(message, title = 'Request failed') {
  return base(COLORS.red, title).setDescription(clip(message, 1000));
}

export function noticeEmbed(message, title = 'Notice') {
  return base(COLORS.sky, title).setDescription(clip(message, 2000));
}

function playLine(p, i) {
  const who = p.player || p.matchup || 'unknown';
  const market = [p.stat, p.side, p.line !== null && p.line !== undefined ? formatNum(p.line) : null]
    .filter(Boolean).join(' ');
  const shadow = p.validated === false ? ' [paper]' : '';
  return `**${i + 1}. ${clip(who, 40)}**${market ? ` - ${clip(market, 40)}` : ''}${shadow}\n`
    + `${p.book || 'book n/a'} ${p.price_text || 'n/a'} | EV ${formatPct(p.ev, { signed: true })} | `
    + `fair ${formatPct(p.p_fair)} | ${p.n_books ?? '?'} books`;
}

/** Top +EV dislocations embed. plays: normalized plays (see beezyApi.normalizeEvFeed). */
export function evEmbed(plays, { sport = 'all', book = null, asOf = null } = {}) {
  const title = `+EV dislocations - ${sport === 'all' ? 'all sports' : sport.toUpperCase()}${book ? ` - ${book}` : ''}`;
  const embed = base(COLORS.emerald, title);
  if (!plays.length) {
    return embed.setDescription('No live +EV dislocations clear the minimum edge right now. The feed refreshes every few minutes.');
  }
  embed.setDescription(clip(plays.map(playLine).join('\n\n'), 4000));
  const anyShadow = plays.some((p) => p.validated === false);
  embed.addFields({
    name: 'Notes',
    value: clip(
      'EV is net of de-vig against the consensus of the other books, at the quoted price.'
      + (anyShadow ? ' Rows tagged [paper] come from unvalidated shadow sweeps.' : '')
      + (asOf ? ` As of ${asOf}.` : ''),
      1000,
    ),
  });
  return embed;
}

function projLine(r, i) {
  const matchup = r.team && r.opp ? ` (${r.team} vs ${r.opp})` : r.team ? ` (${r.team})` : '';
  const score = r.beezy_score !== null && r.beezy_score !== undefined ? formatNum(r.beezy_score, 0) : 'n/a';
  return `**${i + 1}. ${clip(r.player || 'unknown', 36)}**${matchup} - ${r.stat || 'stat'} ${formatNum(r.line)} ${r.side || ''}`.trimEnd() + '\n'
    + `model P(over) ${formatPct(r.p_over_model)} | mu ${formatNum(r.proj_mean, 2)} | `
    + `L5 ${formatPct(r.l5_hit_rate, { digits: 0 })} L10 ${formatPct(r.l10_hit_rate, { digits: 0 })} | Beezy ${score}/100`;
}

/** Projections embed. data: {sport, as_of, engine, validated, status, rows}. */
export function projEmbed(data, rows, { sport, player = null, market = null } = {}) {
  const title = `Projections - ${String(sport || data?.sport || '').toUpperCase()}${player ? ` - ${player}` : ''}${market ? ` - ${market}` : ''}`;
  const embed = base(COLORS.sky, title);
  if (!rows.length) {
    return embed.setDescription('No projection rows matched. Try a broader player name or a different market.');
  }
  embed.setDescription(clip(rows.map(projLine).join('\n\n'), 4000));
  const notes = [];
  if (data?.validated === false) notes.push('Projections are paper / unvalidated.');
  if (data?.status && data.status !== 'live') notes.push(`Status: ${data.status}.`);
  if (data?.engine) notes.push(`Engine: ${data.engine}.`);
  if (data?.as_of) notes.push(`As of ${data.as_of}.`);
  if (notes.length) embed.addFields({ name: 'Notes', value: clip(notes.join(' '), 1000) });
  return embed;
}

function trend(values) {
  const nums = values.filter((v) => Number.isFinite(Number(v))).map(Number);
  if (nums.length < 4) return 'n/a';
  const half = Math.floor(nums.length / 2);
  const older = nums.slice(0, half);
  const newer = nums.slice(half);
  const avg = (a) => a.reduce((s, v) => s + v, 0) / a.length;
  const delta = avg(newer) - avg(older);
  if (Math.abs(delta) < 0.05 * Math.max(1, Math.abs(avg(older)))) return 'flat';
  return delta > 0 ? 'rising' : 'falling';
}

/** Cockpit embed for one row (CockpitRow): L10 log, usage trend, odds matrix. */
export function edgeEmbed(row) {
  const form = row.recentForm;
  const side = row.side ? ` ${row.side}` : '';
  const embed = base(COLORS.sky, `${row.player || 'Player'} - ${row.stat || 'prop'} ${formatNum(row.line)}${side}`);
  const meta = [
    row.matchup || row.team,
    row.book ? `${row.book} ${formatAmerican(row.american)}` : null,
    row.ev !== null && row.ev !== undefined ? `EV ${formatPct(row.ev, { signed: true })}` : null,
    row.p_fair !== null && row.p_fair !== undefined ? `fair ${formatPct(row.p_fair)}` : null,
  ].filter(Boolean).join(' | ');
  if (meta) embed.setDescription(clip(meta, 500));

  if (form && Array.isArray(form.games) && form.games.length) {
    const rows = form.games.map((g) => [
      g.date ? String(g.date).slice(5) : '--',
      g.opp || '--',
      formatNum(g.val),
      g.usage === null || g.usage === undefined ? '--' : formatNum(g.usage),
      g.hit === true ? 'HIT' : g.hit === false ? 'MISS' : '--',
    ]);
    embed.addFields({
      name: `Last ${form.games.length} vs line ${formatNum(form.line ?? row.line)}`,
      value: codeTable(['date', 'opp', 'val', form.usage_label ? 'use' : 'usage', 'res'], rows, 1000),
    });
    embed.addFields({
      name: 'Form',
      value: clip(
        `L5 ${formatPct(form.l5_rate, { digits: 0 })} | L10 ${formatPct(form.l10_rate, { digits: 0 })} | `
        + `season avg ${formatNum(form.szn_avg, 2)} | usage trend ${trend(form.games.map((g) => g.usage))}`
        + (form.usage_label ? ` (${form.usage_label})` : ''),
        1000,
      ),
    });
  } else {
    embed.addFields({ name: 'Last 10', value: 'No game log available for this player yet.' });
  }

  if (Array.isArray(row.all_books) && row.all_books.length) {
    const rows = row.all_books.slice(0, 10).map((b) => [
      String(b.book || '?').slice(0, 14),
      formatAmerican(b.over),
      formatAmerican(b.under),
      formatPct(b.fair),
    ]);
    embed.addFields({ name: 'Books', value: codeTable(['book', 'over', 'under', 'fair'], rows, 1000) });
  }
  if (row.shadow) {
    embed.addFields({ name: 'Notes', value: 'Shadow signal: evidence only, unvalidated. No stake sizing.' });
  }
  return embed;
}

/** Track-record embed from the analytics deep-dive. Shows only fields the API returned. */
export function recordEmbed(headline, perSport = [], { system = null } = {}) {
  const embed = base(COLORS.emerald, `Verified paper track record${system ? ` - ${system}` : ''}`);
  if (!headline) {
    return embed.setColor(COLORS.sky).setDescription('The API returned no headline stats yet.');
  }
  const lines = [];
  const add = (label, value) => { if (value !== null && value !== undefined && value !== 'n/a') lines.push(`${label}: ${value}`); };
  if (headline.graded !== undefined) {
    const w = headline.wins ?? '?';
    const l = headline.losses ?? '?';
    const p = headline.pushes ?? 0;
    add('Graded', `${headline.graded} (${w}W-${l}L-${p}P)`);
  }
  if (headline.logged !== undefined) add('Logged', headline.logged);
  if (headline.pending !== undefined) add('Pending', headline.pending);
  if (headline.flat_roi_pct !== undefined) add('Flat-stake ROI', formatPctPoints(headline.flat_roi_pct));
  if (headline.kelly_roi_pct !== undefined) {
    const mult = headline.kelly_mult !== undefined ? ` (kelly mult ${formatNum(headline.kelly_mult, 2)})` : '';
    add('Kelly ROI', `${formatPctPoints(headline.kelly_roi_pct)}${mult}`);
  }
  if (headline.first_date) add('Since', `${headline.first_date}${headline.last_date ? ` to ${headline.last_date}` : ''}`);
  embed.setDescription(clip(lines.join('\n'), 2000) || 'No stats returned.');

  if (perSport.length) {
    const rows = perSport.map((s) => [
      s.sport.toUpperCase(),
      String(s.headline.graded ?? 'n/a'),
      formatPctPoints(s.headline.flat_roi_pct),
      formatPctPoints(s.headline.kelly_roi_pct),
    ]);
    embed.addFields({ name: 'By sport', value: codeTable(['sport', 'graded', 'flat', 'kelly'], rows, 1000) });
  }
  embed.addFields({
    name: 'Notes',
    value: 'PAPER tracking at the flagged price: no slippage, correlated across books, voids excluded from ROI. Not a performance guarantee.',
  });
  return embed;
}

export function recordButtons() {
  return linkButtons([{ label: 'Open Analytics', url: `${siteUrl()}/analytics` }]);
}

export function welcomeEmbed(memberLabel) {
  const site = siteUrl();
  return base(COLORS.emerald, 'Welcome to BEEZY.FYI')
    .setDescription(clip(
      `${memberLabel}, this server is the Discord side of ${site}. BEEZY.FYI runs two engines:\n\n`
      + '**+EV Consensus Terminal** - compares sportsbook and exchange quotes to a de-vigged consensus of the other books and flags the ones that sit off it.\n'
      + '**Calibrated Prop Models** - player prop projections with model probabilities, L5/L10 hit rates and a 0-100 Beezy Score.\n\n'
      + 'Everything is tracked on paper at the flagged price. Nothing here is a guarantee.',
      2000,
    ))
    .addFields(
      {
        name: 'Commands',
        value: '`/ev` live +EV dislocations\n`/proj` today\'s projections\n`/ask` ask the analyst a question\n`/link` sync a beezy.fyi subscription to your roles',
      },
      { name: 'Links', value: `${site}/ev\n${site}/edge\n${site}/results` },
    );
}
