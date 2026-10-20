"""Design system of the web UI: tokens, Gradio theme, icons and the small client-side script.

Dark, near-black surfaces, one cool-blue accent, hairline borders, 8/10/16 px radii,
Inter + JetBrains Mono, a glass header and a docs-style sidebar. The component styles
live in assets/ui/style.css and read the CSS variables defined from TOKENS here.
"""
from __future__ import annotations

import config

UI_DIR = config.ASSETS_DIR / "ui"
ICONS_DIR = UI_DIR / "icons"
CSS_FILE = UI_DIR / "style.css"

TOKENS = {
    "page": "#030303",
    "surface": "#050505",
    "surface-subtle": "#0a0a0a",
    "field": "#080808",
    "secondary": "#121212",
    "text": "#f3f3f5",
    "text-secondary": "#a1a1aa",
    "text-muted": "#8a8a8a",
    "link": "#89abff",
    "primary": "#82a2ff",
    "primary-hover": "#8bacff",
    "primary-pressed": "#80a0ff",
    "on-primary": "#05070c",
    "primary-soft": "#82a2ff2e",
    "border": "#ffffff1a",
    "border-card": "#34343473",
    "divider": "#202020",
    "focus": "#80a0ffc7",
    "hover": "#ffffff0a",
    "info": "#5ba4ff",
    "success": "#42d780",
    "warning": "#f2b038",
    "error": "#ff5b5b",
    "radius-control": "8px",
    "radius-field": "10px",
    "radius-card": "16px",
    "radius-small": "6px",
    "header-height": "64px",
    "dock-h": "56px",
    "ease-out": "cubic-bezier(.16, 1, .3, 1)",
}

# simple line icons (24×24, 2px round strokes)
_ICON_PATHS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="m10.9 12.1 8.1-8.1"/><path d="m17 6 2.5 2.5"/><path d="m14 9 2 2"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "send": '<path d="m22 2-11 11"/><path d="m22 2-7 20-4-9-9-4z"/>',
    "rotate": '<path d="M3 12a9 9 0 1 0 2.64-6.36"/><path d="M3 3v6h6"/>',
    "sparkles": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 17v4"/><path d="M17 19h4"/>',
    "play": '<path d="M7 4.5v15l12.5-7.5z"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "film": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18"/><path d="M17 3v18"/><path d="M3 8h4"/><path d="M3 16h4"/><path d="M17 8h4"/><path d="M17 16h4"/>',
    "menu": '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/>',
    "scissors": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9"/><path d="M14.5 14.5 20 20"/><path d="M8.1 8.1 12 12"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "arrow-up": '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "trash": '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    "list": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
    "merge": '<path d="M8 6h8"/><path d="M8 18h8"/><path d="M12 6v12"/><path d="m9 15 3 3 3-3"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
}
_TONES = {"muted": TOKENS["text-secondary"], "text": TOKENS["text"], "dark": TOKENS["on-primary"]}


def svg(name: str, color: str = "currentColor", size: int = 16) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{_ICON_PATHS[name]}</svg>')


def icon(name: str, tone: str = "muted") -> str:
    """Path of an icon file for gr.Button(icon=…), generated on first use."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    path = ICONS_DIR / f"{name}-{tone}.svg"
    if not path.exists():
        path.write_text(svg(name, _TONES[tone], 24))
    return str(path)


def css() -> str:
    tokens = "\n".join(f"  --cdm-{k}: {v};" for k, v in TOKENS.items())
    # move-type colours: the same as the cards in the video (config.MOVE_TYPES)
    types = "\n".join(f".mt-{k}, label:has(> .cdm-rich.mt-{k}) {{ --mt: {st.color}; }}" for k, st in config.MOVE_TYPES.items())
    return f":root, .dark {{\n{tokens}\n}}\n{types}\n" + CSS_FILE.read_text()


def theme():
    import gradio as gr

    t = TOKENS
    base = gr.themes.Base(
        primary_hue=gr.themes.colors.blue,
        neutral_hue=gr.themes.colors.zinc,
        font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
        font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "ui-monospace", "monospace"],
        radius_size=gr.themes.sizes.radius_md,
    )
    values = {
        "body_background_fill": t["page"],
        "body_text_color": t["text"],
        "body_text_color_subdued": t["text-secondary"],
        "body_text_size": "15px",
        "background_fill_primary": t["surface"],
        "background_fill_secondary": t["surface-subtle"],
        "border_color_primary": t["border"],
        "border_color_accent": t["primary"],
        "border_color_accent_subdued": t["primary-soft"],
        "color_accent": t["primary"],
        "color_accent_soft": t["primary-soft"],
        "link_text_color": t["link"],
        "link_text_color_hover": t["primary-hover"],
        "link_text_color_active": t["primary"],
        "link_text_color_visited": t["link"],
        "code_background_fill": t["secondary"],
        "shadow_drop": "none",
        "shadow_drop_lg": "0 24px 64px rgba(0,0,0,.55)",
        "shadow_spread": "3px",
        "block_background_fill": "transparent",
        "block_border_width": "0px",
        "block_border_color": "transparent",
        "block_padding": "0px",
        "block_radius": t["radius-card"],
        "block_shadow": "none",
        "block_label_background_fill": "transparent",
        "block_label_border_width": "0px",
        "block_label_text_color": t["text-secondary"],
        "block_label_text_size": "13px",
        "block_label_text_weight": "500",
        "block_label_padding": "0 0 6px 0",
        "block_label_margin": "0",
        "block_title_background_fill": "transparent",
        "block_title_text_color": t["text-secondary"],
        "block_title_text_size": "13px",
        "block_title_text_weight": "500",
        "block_title_padding": "0 0 6px 0",
        "block_info_text_color": t["text-muted"],
        "container_radius": t["radius-card"],
        "layout_gap": "16px",
        "form_gap_width": "0px",
        "panel_background_fill": t["surface-subtle"],
        "panel_border_color": t["border-card"],
        "panel_border_width": "1px",
        "accordion_text_color": t["text"],
        "input_background_fill": t["field"],
        "input_background_fill_focus": t["field"],
        "input_background_fill_hover": t["field"],
        "input_border_color": t["border"],
        "input_border_color_hover": "#ffffff2e",
        "input_border_color_focus": t["focus"],
        "input_border_width": "1px",
        "input_radius": t["radius-field"],
        "input_padding": "9px 12px",
        "input_placeholder_color": t["text-muted"],
        "input_shadow": "none",
        "input_shadow_focus": f"0 0 0 3px {t['primary-soft']}",
        "input_text_size": "14px",
        "checkbox_background_color": t["field"],
        "checkbox_background_color_hover": t["field"],
        "checkbox_background_color_focus": t["field"],
        "checkbox_background_color_selected": t["primary"],
        "checkbox_border_color": "#ffffff33",
        "checkbox_border_color_hover": "#ffffff4d",
        "checkbox_border_color_focus": t["focus"],
        "checkbox_border_color_selected": t["primary"],
        "checkbox_border_radius": "5px",
        "checkbox_label_background_fill": "transparent",
        "checkbox_label_background_fill_hover": "transparent",
        "checkbox_label_background_fill_selected": "transparent",
        "checkbox_label_border_width": "0px",
        "checkbox_label_padding": "4px 0",
        "checkbox_label_shadow": "none",
        "checkbox_label_text_size": "14px",
        "checkbox_label_text_color": t["text"],
        "checkbox_label_text_color_selected": t["text"],
        "slider_color": t["primary"],
        "loader_color": t["primary"],
        "error_background_fill": "#ff5b5b14",
        "error_border_color": "#ff5b5b55",
        "error_text_color": t["error"],
        "button_border_width": "1px",
        "button_transition": f"background .15s {t['ease-out']}, border-color .15s, color .15s, transform .15s",
        "button_transform_hover": "none",
        "button_transform_active": "scale(.98)",
        "button_small_radius": t["radius-control"],
        "button_medium_radius": t["radius-control"],
        "button_large_radius": t["radius-control"],
        "button_small_padding": "6px 10px",
        "button_medium_padding": "8px 14px",
        "button_large_padding": "11px 18px",
        "button_small_text_size": "13px",
        "button_medium_text_size": "14px",
        "button_large_text_size": "15px",
        "button_small_text_weight": "500",
        "button_medium_text_weight": "500",
        "button_large_text_weight": "600",
        "button_primary_background_fill": t["primary"],
        "button_primary_background_fill_hover": t["primary-hover"],
        "button_primary_border_color": t["primary"],
        "button_primary_border_color_hover": t["primary-hover"],
        "button_primary_text_color": t["on-primary"],
        "button_primary_text_color_hover": t["on-primary"],
        "button_primary_shadow": "none",
        "button_primary_shadow_hover": "0 0 0 4px #82a2ff1f",
        "button_secondary_background_fill": "transparent",
        "button_secondary_background_fill_hover": t["hover"],
        "button_secondary_border_color": t["border"],
        "button_secondary_border_color_hover": "#ffffff2e",
        "button_secondary_text_color": t["text"],
        "button_secondary_text_color_hover": t["text"],
        "button_secondary_shadow": "none",
        "button_cancel_background_fill": "transparent",
        "button_cancel_background_fill_hover": "#ff5b5b14",
        "button_cancel_border_color": "#ff5b5b55",
        "button_cancel_text_color": t["error"],
        "table_border_color": t["border"],
        "table_even_background_fill": t["surface"],
        "table_odd_background_fill": t["surface-subtle"],
        "stat_background_fill": t["primary-soft"],
    }
    # the UI is dark-only: light and dark mode get the same values (where a _dark variant exists)
    import inspect

    known = set(inspect.signature(base.set).parameters)
    return base.set(**values, **{f"{k}_dark": v for k, v in values.items() if f"{k}_dark" in known})


# client-side behaviour that needs no server round trip
HEAD = """
<meta name="theme-color" content="#030303">
<script>
/* Rich list rows without touching Svelte's nodes: the original label text stays (hidden) and a
   sibling copy is rebuilt whenever that text changes, so updated choices never show stale rows. */
window.cdmRich = (orig, build) => {
  let rich = orig.nextElementSibling;
  if (!rich || !rich.classList.contains('cdm-rich')) {
    rich = document.createElement('span');
    rich.className = 'cdm-rich';
    orig.after(rich);
  }
  const text = orig.textContent;
  if (rich.dataset.src === text) return;
  rich.dataset.src = text;
  rich.textContent = '';
  rich.className = 'cdm-rich';
  if (build(rich, text) === false) { orig.classList.remove('cdm-orig'); rich.remove(); return; }
  orig.classList.add('cdm-orig');
};
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  // Gradio prefixes custom CSS with ".gradio-container .contain", so the state class must sit on an
  // element *inside* .contain that wraps both the header (backdrop) and the sidebar
  const host = () => $('.gradio-container .contain > *') || document.body;
  const closeNav = () => host().classList.remove('nav-open');
  document.addEventListener('click', (e) => {
    const det = e.target.closest('.cdm-details-toggle');
    if (det) { const open = host().classList.toggle('details-open'); det.setAttribute('aria-expanded', String(open)); return; }
    if (e.target.closest('.cdm-hamburger')) { host().classList.toggle('nav-open'); return; }
    if (e.target.closest('.cdm-backdrop')) { closeNav(); return; }
    if (window.innerWidth <= 860 && e.target.closest('#sidebar a, #sidebar label, #sidebar button')) setTimeout(closeNav, 150);
  });
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      const box = $('#hdr-search textarea, #hdr-search input'); if (box) { e.preventDefault(); box.focus(); box.select(); }
    }
    if (e.key === 'Escape') { closeNav(); host().classList.remove('details-open'); const x = $('#search-close'); if (x && $('#search-modal')) x.click(); }
  });
  const TONES = [['❌', 'error'], ['⚠', 'warning'], ['✅', 'success'],
                 ...['⏳', '📥', '🎙', '👥', '🧠', '🖼', '🎯', '🎬', '📤', '✂', '⬇'].map((p) => [p, 'progress'])];
  const BADGES = {'готово': 'success', 'опубликовано': 'info', 'ошибка': 'error', 'в работе': 'warning',
                  'разобрано': 'info', 'отклонено': 'muted'};
  const decorate = () => {
    const st = $('#status');
    if (st) {
      const t = (st.innerText || '').trim();
      const tone = t ? ((TONES.find(([p]) => t.startsWith(p)) || [0, 'info'])[1]) : 'empty';
      if (st.dataset.tone !== tone) st.dataset.tone = tone;
      const dock = $('#dock'); if (dock && dock.dataset.tone !== tone) dock.dataset.tone = tone;
    }
    document.querySelectorAll('#jobs-nav label > span:not(.cdm-rich)').forEach((sp) => {
      window.cdmRich && cdmRich(sp, (rich, text) => {
        const parts = text.split(' · ');
        if (parts.length < 3) return false;
        const [title, when, state] = parts;
        const a = document.createElement('span'); a.className = 'nav-job-title'; a.textContent = title;
        const m = document.createElement('span'); m.className = 'nav-job-meta'; m.textContent = when + ' ';
        const b = document.createElement('span'); b.className = 'cdm-badge cdm-badge-' + (BADGES[state] || 'muted'); b.textContent = state;
        m.appendChild(b); rich.append(a, m);
        return true;
      });
    });
    const links = [...document.querySelectorAll('#sidebar a.nav-link[href^="#"]')];
    if (links.length) {
      let current = links[0];
      for (const l of links) { const sec = document.getElementById(l.getAttribute('href').slice(1)); if (sec && sec.getBoundingClientRect().top < 140) current = l; }
      links.forEach((l) => l.classList.toggle('active', l === current));
    }
  };
  new MutationObserver(decorate).observe(document.documentElement, {subtree: true, childList: true, characterData: true});
  document.addEventListener('scroll', decorate, {passive: true});
})();
</script>
<script>
/* ---- editor helpers: players, timelines, transport, shortcuts, rich list rows, page hashes ---- */
window.cdm = (() => {
  const $ = (s, r = document) => r.querySelector(s);
  const parse = (t) => {
    if (typeof t === 'number') return t;
    const s = String(t || '').trim().replace(',', '.');
    if (!s) return NaN;
    return s.split(':').reduce((acc, part) => acc * 60 + parseFloat(part || 0), 0);
  };
  const fmt = (sec) => {
    if (!isFinite(sec)) return '';
    sec = Math.max(0, sec);
    const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec - Math.floor(sec / 60) * 60;
    const ss = s.toFixed(1).padStart(4, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
  };
  const video = (sel) => $(sel + ' video');
  let stopAt = null;
  const api = {
    parse, fmt, video,
    now: (sel) => { const v = video(sel); return v ? fmt(v.currentTime) : ''; },
    nowSec: (sel) => { const v = video(sel); return v ? Math.round(v.currentTime * 100) / 100 : 0; },
    seek: (sel, t, play) => {
      const v = video(sel), s = parse(t);
      if (!v || isNaN(s)) return;
      v.currentTime = Math.max(0, s);
      if (play) v.play();
    },
    toggle: (sel) => { const v = video(sel); if (v) (v.paused ? v.play() : v.pause()); },
    nudge: (sel, d) => { const v = video(sel); if (v) v.currentTime = Math.max(0, Math.min(v.duration || 1e9, v.currentTime + d)); },
    playRange: (sel, a, b) => {
      const v = video(sel), s = parse(a), e = parse(b);
      if (!v || isNaN(s)) return;
      stopAt = isNaN(e) ? null : { v, e };
      v.currentTime = s; v.play();
    },
    activePlayer: () => ['#manual-player', '#moves-player', '#sounds-player'].find((sel) => { const el = $(sel); return el && el.offsetParent; }),
  };
  // playhead + time readout; stop at the end of "play this line"
  document.addEventListener('timeupdate', (e) => {
    const v = e.target;
    if (!(v instanceof HTMLVideoElement)) return;
    const host = v.closest('[id$="-player"]');
    if (!host) return;
    const sel = '#' + host.id;
    if (stopAt && stopAt.v === v && v.currentTime >= stopAt.e) { v.pause(); stopAt = null; }
    document.querySelectorAll(`.cdm-tl[data-player="${sel}"]`).forEach((tl) => {
      const d = parseFloat(tl.dataset.duration) || v.duration || 1;
      tl.querySelectorAll('.cdm-playhead').forEach((ph) => { ph.style.left = Math.min(100, 100 * v.currentTime / d) + '%'; });
    });
    document.querySelectorAll(`.cdm-tp-time[data-player="${sel}"]`).forEach((x) => {
      x.textContent = fmt(v.currentTime) + (isFinite(v.duration) ? ' / ' + fmt(v.duration) : '');
    });
  }, true);
  const pick = (list, value) => {
    if (!list || !value) return;
    const inp = document.querySelector(`${list} input[value="${CSS.escape(value)}"]`);
    if (inp && !inp.checked) inp.click();
  };
  document.addEventListener('click', (e) => {
    const tp = e.target.closest('.cdm-transport .cdm-tp');  // the trim editor's buttons are handled by cdmTrim
    if (tp) {
      const sel = tp.closest('.cdm-transport').dataset.player, cmd = tp.dataset.cmd;
      if (cmd === 'play') api.toggle(sel); else api.nudge(sel, parseFloat(cmd));
      return;
    }
    const seg = e.target.closest('.cdm-tl-seg, .cdm-card[data-value]');
    if (seg) {
      const tl = seg.closest('.cdm-tl');
      const sel = tl ? tl.dataset.player : api.activePlayer();
      if (sel && seg.dataset.start) api.seek(sel, parseFloat(seg.dataset.start));
      pick(seg.dataset.list, seg.dataset.value);
      return;
    }
    const track = e.target.closest('.cdm-tl-track');
    if (track) {
      const tl = track.closest('.cdm-tl'), r = track.getBoundingClientRect();
      api.seek(tl.dataset.player, (e.clientX - r.left) / r.width * parseFloat(tl.dataset.duration));
    }
  });
  // keyboard: only while an editor page is visible and the focus is not in a field
  const fieldOf = (ids) => ids.map((id) => $(`#${id} textarea, #${id} input`)).find((el) => el && el.offsetParent);
  const setField = (el, value) => {
    if (!el) return;
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  document.addEventListener('keydown', (e) => {
    const sel = api.activePlayer();
    if (!sel || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && (t.closest('input, textarea, select, [contenteditable="true"]'))) return;
    if (e.code === 'Space') { e.preventDefault(); api.toggle(sel); }
    else if (e.code === 'ArrowLeft') { e.preventDefault(); api.nudge(sel, e.shiftKey ? -5 : -1); }
    else if (e.code === 'ArrowRight') { e.preventDefault(); api.nudge(sel, e.shiftKey ? 5 : 1); }
    else if (e.code === 'KeyI') setField(fieldOf(['l-start', 'm-start', 'e-start']), api.now(sel));
    else if (e.code === 'KeyO') setField(fieldOf(['l-end', 'm-end', 'e-end']), api.now(sel));
    else if (e.code === 'KeyN') { const b = ['#ln-add', '#mv-add', '#snd-add'].map((q) => $(q)).find((x) => x && x.offsetParent); if (b) b.click(); }
  });
  // rich rows for the editor lists: labels are fields joined by U+2063
  const SEP = '\u2063';
  const NAMES = { white: 'Б', black: 'Ч' };
  const row = (sp, parts, kind) => {
    const add = (cls, text) => { const el = document.createElement('span'); el.className = cls; el.textContent = text; sp.appendChild(el); return el; };
    if (kind === 'moves') {
      const [n, time, type, title] = parts;
      sp.classList.add('mt-' + type);
      add('cdm-row-n', n); add('cdm-row-title', title); add('cdm-row-time', time);
    } else if (kind === 'lines') {
      const [time, spk, star, strength, moment, text] = parts;
      add('cdm-row-time', time);
      add('cdm-spk cdm-spk-' + (spk === '-' ? 'none' : spk), NAMES[spk] || '?');
      add('cdm-row-title', (star ? '★ ' : '') + text);
      if (strength) add('cdm-row-badge ' + (strength.startsWith('+') ? 'is-plus' : 'is-minus'), strength);
      if (moment) add('cdm-row-moment', '♟' + moment);
    } else if (kind === 'sounds') {
      const [time, cat, label, who, unsure, inc] = parts;
      sp.classList.add('snd-' + cat);
      add('cdm-row-time', time);
      add('cdm-snd-dot', '');
      add('cdm-row-title', label + (who && who !== '-' ? ' · ' + who : ''));
      if (unsure) add('cdm-row-badge is-guess', 'предположение');
      if (inc) add('cdm-row-badge is-plus', 'в видео');
    } else {
      const [n, type, title, time, warn] = parts;
      sp.classList.add('mt-' + type);
      add('cdm-row-n', n); add('cdm-row-title', title); add('cdm-row-time', time);
      if (warn) add('cdm-row-warn', '!');
    }
  };
  const decorateLists = () => {
    for (const [list, kind] of [['#moves-list', 'moves'], ['#lines-list', 'lines'], ['#moments-list', 'moments'], ['#sounds-list', 'sounds']]) {
      document.querySelectorAll(`${list} label > span:not(.cdm-rich)`).forEach((sp) => {
        window.cdmRich(sp, (rich, text) => {
          const parts = text.split(SEP).map((x) => x.trim());
          if (parts.length < 4) return false;
          row(rich, parts, kind);
          return true;
        });
      });
    }
  };
  new MutationObserver(decorateLists).observe(document.documentElement, { subtree: true, childList: true, characterData: true });
  // pages have their own address: #moves, #manual (opening such a link switches the page)
  document.addEventListener('change', (e) => {
    const inp = e.target.closest('#pages-nav input');
    if (inp) history.replaceState(null, '', inp.value === 'create' ? location.pathname : '#' + inp.value);
  });
  // Gradio adds this <head> after the page's load event, so start right away and retry until the app answers
  const route = (tries = 0) => {
    const want = location.hash.replace('#', '');
    if (!want || want === 'create' || !['moves', 'manual', 'sounds', 'style'].includes(want) || tries > 25) return;
    const inp = $(`#pages-nav input[value="${CSS.escape(want)}"]`);
    if (inp && inp.checked && $(`#${want}-title`)?.offsetParent) return;
    if (inp && tries > 2) inp.click();
    setTimeout(() => route(tries + 1), 700);
  };
  if (document.readyState === 'complete') route(); else window.addEventListener('load', () => route());
  window.addEventListener('hashchange', () => route(3));
  return api;
})();
</script>
<script>
/* ---- trimming editor of the «Источник» section (ui_trim.py builds the HTML) ---- */
window.cdmTrim = (() => {
  const $ = (s, r = document) => r.querySelector(s);
  const parse = (t) => { const s = String(t || '').trim().replace(',', '.'); if (!s) return NaN;
    return s.split(':').reduce((a, p) => a * 60 + parseFloat(p || 0), 0); };
  const fmt = (sec) => { if (!isFinite(sec)) return ''; sec = Math.max(0, sec);
    const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec - Math.floor(sec / 60) * 60;
    const ss = s.toFixed(1).padStart(4, '0'); return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`; };
  const field = (which) => $(`#cut-${which} textarea, #cut-${which} input`);
  const setField = (el, v) => { if (!el) return; el.value = v; el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true })); };
  let ytReady = null;
  const loadYT = () => ytReady || (ytReady = new Promise((ok) => {
    if (window.YT && YT.Player) return ok();
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => { prev && prev(); ok(); };
    const tag = document.createElement('script'); tag.src = 'https://www.youtube.com/iframe_api'; document.head.appendChild(tag);
  }));
  const players = new WeakMap();
  const adapter = (root) => {
    if (players.has(root)) return players.get(root);
    let api;
    if (root.dataset.kind === 'video') {
      const v = $('video', root);
      api = { time: () => v.currentTime, seek: (t) => { v.currentTime = Math.max(0, t); }, play: () => v.play(), pause: () => v.pause(),
              duration: () => (isFinite(v.duration) ? v.duration : 0), playing: () => !v.paused };
    } else {
      let p = null;
      api = { time: () => (p && p.getCurrentTime ? p.getCurrentTime() : 0), seek: (t) => p && p.seekTo && p.seekTo(Math.max(0, t), true),
              play: () => p && p.playVideo && p.playVideo(), pause: () => p && p.pauseVideo && p.pauseVideo(),
              duration: () => (p && p.getDuration ? p.getDuration() : 0), playing: () => !!(p && p.getPlayerState && p.getPlayerState() === 1) };
      loadYT().then(() => {
        const el = $('.cdm-trim-yt', root);
        if (!el) return;
        p = new YT.Player(el, { videoId: root.dataset.vid, playerVars: { rel: 0, modestbranding: 1, playsinline: 1, origin: location.origin },
                                events: { onReady: () => draw(root), onError: () => {
                                  const n = $('.cdm-trim-note', root);
                                  if (n) n.textContent = 'YouTube не показывает этот ролик во встроенном плеере (он недоступен или автор запретил встраивание). '
                                    + 'Нажмите «Скачать для точной обрезки» — предпросмотр откроется из скачанного файла.';
                                  root.classList.add('cdm-trim-failed'); } } });
      });
    }
    players.set(root, api);
    return api;
  };
  const dur = (root) => parseFloat(root.dataset.duration) || adapter(root).duration() || 0;
  const range = (root) => {
    const d = dur(root);
    let a = parse(field('start') && field('start').value), b = parse(field('end') && field('end').value);
    if (!isFinite(a)) a = 0; if (!isFinite(b) || b <= 0) b = d;
    return [Math.max(0, a), d ? Math.min(d, b) : b];
  };
  const draw = (root) => {
    const d = dur(root); if (!d) return;
    const [a, b] = range(root);
    const pa = 100 * a / d, pb = 100 * b / d;
    $('.cdm-trim-h.is-start', root).style.left = pa + '%';
    $('.cdm-trim-h.is-end', root).style.left = pb + '%';
    const sel = $('.cdm-trim-sel', root); sel.style.left = pa + '%'; sel.style.width = Math.max(0, pb - pa) + '%';
    const whole = a <= 0.05 && b >= d - 0.05;
    $('.cdm-trim-info', root).textContent = whole ? `Весь ролик · ${fmt(d)}` : `${fmt(a)} – ${fmt(b)} · ${(b - a).toFixed(1)} с`;
    const ticks = $('.cdm-trim-ticks', root);
    if (ticks.dataset.d !== String(d)) {
      ticks.dataset.d = String(d); ticks.textContent = '';
      const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200].find((s) => d / s <= 10) || 1800;
      for (let t = 0; t <= d + 1e-6; t += step) {
        const el = document.createElement('span'); el.className = 'cdm-tl-tick'; el.style.left = (100 * t / d) + '%';
        el.textContent = fmt(t).replace(/[.][0-9]$/, ''); ticks.appendChild(el);
      }
    }
  };
  const write = (root, which, t) => {
    const d = dur(root); const [a, b] = range(root);
    t = Math.max(0, Math.min(d || t, t));
    if (which === 'start') t = Math.min(t, b - 0.3); else t = Math.max(t, a + 0.3);
    const whole = which === 'start' ? t <= 0.05 : d && t >= d - 0.05;
    setField(field(which), whole ? '' : fmt(t));
    draw(root);
  };
  let stopAt = null;
  const init = (root) => {
    if (root.dataset.init) return;
    root.dataset.init = '1';
    const api = adapter(root);
    if (root.dataset.kind === 'video') $('video', root).addEventListener('loadedmetadata', () => draw(root));
    const track = $('.cdm-trim-track', root);
    const at = (e) => { const r = track.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur(root); };
    let drag = null;
    track.addEventListener('pointerdown', (e) => {
      const h = e.target.closest('.cdm-trim-h');
      if (h) { drag = h.classList.contains('is-start') ? 'start' : 'end'; track.setPointerCapture(e.pointerId); e.preventDefault(); }
      else api.seek(at(e));
    });
    track.addEventListener('pointermove', (e) => { if (drag) { const t = at(e); write(root, drag, t); api.seek(t); } });
    track.addEventListener('pointerup', () => { drag = null; });
    root.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-act]'); if (!b) return;
      const act = b.dataset.act, which = b.dataset.which;
      const [a, z] = range(root);
      if (act === 'set') write(root, which, api.time());
      else if (act === 'nudge') { const t = (which === 'start' ? a : z) + parseFloat(b.dataset.d); write(root, which, t); api.seek(t); }
      else if (act === 'play-sel') { api.seek(a); api.play(); stopAt = { root, t: z }; }
      else if (act === 'clear') { setField(field('start'), ''); setField(field('end'), ''); draw(root); }
    });
    draw(root);
  };
  setInterval(() => {
    document.querySelectorAll('.cdm-trim[data-init]').forEach((root) => {
      const api = players.get(root); if (!api) return;
      const d = dur(root); const t = api.time();
      if (d) $('.cdm-trim-ph', root).style.left = Math.min(100, 100 * t / d) + '%';
      $('.cdm-trim-time', root).textContent = fmt(t) + (d ? ' / ' + fmt(d) : '');
      if (stopAt && stopAt.root === root && t >= stopAt.t) { api.pause(); stopAt = null; }
    });
  }, 150);
  document.addEventListener('input', (e) => {  // typed timecodes move the handles
    if (e.target.closest('#cut-start, #cut-end')) document.querySelectorAll('.cdm-trim[data-init]').forEach(draw);
  });
  new MutationObserver(() => document.querySelectorAll('.cdm-trim:not([data-init]):not(.cdm-trim-empty)').forEach(init))
    .observe(document.documentElement, { subtree: true, childList: true });
  return { parse, fmt };
})();
</script>
"""