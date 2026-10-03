"""Main window (customtkinter)."""
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime, timezone
from tkinter import messagebox, ttk

import customtkinter as ctk

from . import APP_NAME, PROJECT_URL, __version__, sound, updater
from .gamedata import LEVELS
from .i18n import LANGS, t
from .paths import USER_DIR

log = logging.getLogger(__name__)
GAME_LANGS = ('auto', 'en', 'de', 'ru')
ACCENT = '#3fb68b'
LEVEL_ICON = {1: '🏆 ', 2: '👑 '}   # trophy / super trophy (by weight)
HELPER_TIMEOUT_S = 45


def fmt_weight(g):
    if not g:
        return '–'
    return f'{g / 1000:.3f} {t("kg")}' if g >= 1000 else f'{g} {t("g")}'


def local_time(iso):
    try:
        return datetime.fromisoformat(iso).astimezone().strftime('%H:%M')
    except Exception:
        return ''


class App(ctk.CTk):
    def __init__(self, settings, store, gamedata, engine_factory, uploader):
        super().__init__()
        self.settings, self.store, self.gd, self.uploader = settings, store, gamedata, uploader
        self.events = queue.Queue()
        self.engine = engine_factory(lambda kind, data: self.events.put((kind, data)))
        self.session_start = datetime.now(timezone.utc).isoformat(timespec='seconds')
        self.session_t0 = time.time()
        self.coords = None            # last (x, y) from the engine
        self._last_status = None      # last status event, re-applied after a language switch
        self.bite_flash_until = 0
        self._not_here = None         # (fish_id, waterbody) of the last catch if that fish doesn't live there
        # update bar: None | available | downloading | installing | failed | rolled_back | done
        self.update_state, self.update_rel, self.update_info = None, None, ''
        if '--updated' in sys.argv:
            self.update_state = 'done'
        elif '--update-failed' in sys.argv:
            self.update_state = 'rolled_back'

        ctk.set_appearance_mode('dark')
        self.title(f'{APP_NAME} {__version__}')
        self.geometry(settings.get('window') or '820x600')
        self.minsize(700, 460)
        try:
            self.iconbitmap(os.path.join(os.path.dirname(__file__), 'assets', 'app.ico'))
        except Exception:
            pass
        self._build()
        self.refresh_table()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.engine.start()
        self.after(100, self._poll)
        if updater.install_dir():
            # leftovers of the last update (the helper may still be closing, so not right away)
            cleanup = threading.Timer(20, updater.cleanup)
            cleanup.daemon = True   # must not keep the process alive - an update helper may be waiting for us
            cleanup.start()
        if self.update_state == 'done':
            self.after(15000, lambda: self.update_state == 'done' and self._set_update_state(None))
        if self.settings.get('auto_update', True) and updater.auto_check_enabled():
            self.after(5000, self._auto_check)

    # ------------------------------------------------------------------ layout
    def _build(self):
        top = ctk.CTkFrame(self, fg_color='transparent')
        top.pack(fill='x', padx=14, pady=(12, 4))
        self.status_dot = ctk.CTkLabel(top, text='●', text_color='#888', font=ctk.CTkFont(size=18))
        self.status_dot.pack(side='left')
        self.status_lbl = ctk.CTkLabel(top, text=t('status_starting'), anchor='w')
        self.status_lbl.pack(side='left', padx=(6, 0))
        self.ui_labels = {'auto': t('auto_system'), **LANGS}
        self.ui_var = tk.StringVar(value=self.ui_labels.get(self.settings.get('ui_lang', 'auto'), t('auto_system')))
        ctk.CTkOptionMenu(top, values=list(self.ui_labels.values()), variable=self.ui_var, width=150,
                          command=self._on_ui_lang).pack(side='right')
        ctk.CTkLabel(top, text='🌐').pack(side='right', padx=(14, 4))
        self.bite_lbl = ctk.CTkLabel(top, text='', text_color='#ffcc33', font=ctk.CTkFont(size=15, weight='bold'))
        self.bite_lbl.pack(side='right', padx=(10, 0))
        self.coord_lbl = ctk.CTkLabel(top, text='📍 ' + self._coord_text(), font=ctk.CTkFont(size=15, weight='bold'))
        self.coord_lbl.pack(side='right')
        self.update_bar = ctk.CTkFrame(self, fg_color='#24463b')
        self._header = top

        row = ctk.CTkFrame(self, fg_color='transparent')
        row.pack(fill='x', padx=14, pady=4)
        ctk.CTkLabel(row, text=t('waterbody')).pack(side='left')
        self.water_ids = sorted(self.gd.waterbodies, key=lambda w: self.gd.water_name(w, self._lang()))
        self.water_names = [self.gd.water_name(w, self._lang()) for w in self.water_ids]
        cur = self.settings.get('waterbody')
        self.water_var = tk.StringVar(value=self.gd.water_name(cur, self._lang()) if cur in self.gd.waterbodies
                                      else t('waterbody_choose'))
        ctk.CTkOptionMenu(row, values=self.water_names, variable=self.water_var, width=220,
                          command=self._on_water).pack(side='left', padx=(6, 16))
        ctk.CTkLabel(row, text=t('game_language')).pack(side='left')
        self.gl_labels = {'auto': t('auto'), **LANGS}
        self.gl_var = tk.StringVar(value=self.gl_labels[self.settings.get('game_lang', 'auto')])
        ctk.CTkOptionMenu(row, values=[self.gl_labels[k] for k in GAME_LANGS], variable=self.gl_var, width=130,
                          command=self._on_game_lang).pack(side='left', padx=6)
        ctk.CTkButton(row, text='⚙', width=36, command=self.open_settings).pack(side='right')
        self.pause_btn = ctk.CTkButton(row, text=t('resume') if self.engine.paused else t('pause'), width=100,
                                       command=self.toggle_pause)
        self.pause_btn.pack(side='right', padx=6)

        self.warn = ctk.CTkLabel(self, text=t('waterbody_missing'), text_color='#ffb454', anchor='w')
        stats = ctk.CTkFrame(self)
        stats.pack(fill='x', padx=14, pady=(6, 6))
        self.stat_vals = {}
        for key in ('stat_catches', 'stat_per_hour', 'stat_trophies', 'stat_session'):
            f = ctk.CTkFrame(stats, fg_color='transparent')
            f.pack(side='left', expand=True, fill='x', pady=6)
            v = ctk.CTkLabel(f, text='0', font=ctk.CTkFont(size=22, weight='bold'))
            v.pack()
            ctk.CTkLabel(f, text=t(key), text_color='#9aa4ad').pack()
            self.stat_vals[key] = v
        self._update_warning()

        style = ttk.Style(self)
        style.theme_use('default')
        style.configure('Treeview', background='#1f2326', fieldbackground='#1f2326', foreground='#e6e6e6',
                        rowheight=26, borderwidth=0, font=('Segoe UI', 10))
        style.configure('Treeview.Heading', background='#2b3035', foreground='#cfd6dc', relief='flat',
                        font=('Segoe UI', 10, 'bold'))
        style.map('Treeview', background=[('selected', '#2f6f5a')])
        table = ctk.CTkFrame(self)
        table.pack(fill='both', expand=True, padx=14, pady=4)
        cols = ('time', 'fish', 'weight', 'length', 'spot', 'cloud')
        self.tree = ttk.Treeview(table, columns=cols, show='headings', selectmode='browse')
        for c, w, anchor in (('time', 60, 'center'), ('fish', 230, 'w'), ('weight', 100, 'e'), ('length', 80, 'e'),
                             ('spot', 120, 'center'), ('cloud', 80, 'center')):
            self.tree.heading(c, text=t('col_' + c))
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == 'fish'))
        self.tree.tag_configure('trophy', foreground='#ffcc33')
        self.tree.tag_configure('super', foreground='#ff9f43')
        self.tree.tag_configure('unknown', foreground='#ff8a65')
        sb = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', lambda e: self.edit_selected())

        bottom = ctk.CTkFrame(self, fg_color='transparent')
        bottom.pack(fill='x', padx=14, pady=(4, 12))
        ctk.CTkButton(bottom, text=t('edit'), width=100, command=self.edit_selected).pack(side='left')
        ctk.CTkButton(bottom, text=t('delete'), width=100, fg_color='#7a3b3b', hover_color='#944848',
                      command=self.delete_selected).pack(side='left', padx=8)
        self.upload_lbl = ctk.CTkLabel(bottom, text='', text_color='#9aa4ad')
        self.upload_lbl.pack(side='right')
        if self._last_status:
            self._show_status(self._last_status)
        self._render_update_bar()

    def _coord_text(self):
        return f'{self.coords[0]}:{self.coords[1]}' if self.coords else t('spot_unknown')

    def _on_ui_lang(self, label):
        """Switch the app's own language right away: rebuild the window, keep everything else running."""
        from . import i18n
        key = next(k for k, v in self.ui_labels.items() if v == label)
        self.settings.set('ui_lang', key)
        i18n.set_lang(key)
        for w in list(self.winfo_children()):
            w.destroy()  # also closes open dialogs - they would still show the old language
        self._build()
        self.refresh_table()

    def _lang(self):
        from . import i18n
        return i18n.current

    # ------------------------------------------------------------------ events from the engine
    def _poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._on_event(kind, data)
        except queue.Empty:
            pass
        if self.bite_flash_until and time.time() > self.bite_flash_until:
            self.bite_lbl.configure(text='')
            self.bite_flash_until = 0
        self.after(100, self._poll)

    def _on_event(self, kind, data):
        if kind == 'status':
            self._last_status = data
            self._show_status(data)
        elif kind == 'coords':
            self.coords = data
            self.coord_lbl.configure(text='📍 ' + self._coord_text())
        elif kind == 'bite':
            self.bite_lbl.configure(text='🎣 ' + t('bite'))
            self.bite_flash_until = time.time() + 6
        elif kind == 'catch':
            self.bite_lbl.configure(text='')
            # a fish that doesn't live here: most likely the wrong waterbody is selected (the server rejects it)
            fid, water = data.get('fish_id'), data.get('waterbody')
            self._not_here = (fid, water) if fid and water and not self.gd.lives_in(fid, water) else None
            self._update_warning()
            self.refresh_table()
            self.uploader.kick()
            self.after(3000, self.refresh_table)
        elif kind == 'uploaded':
            self.refresh_table()
        elif kind == 'error':
            self.status_lbl.configure(text=str(data)[:120])
        elif kind == 'update_check':
            rel, manual, err = data
            if rel and self.update_state not in ('downloading', 'installing'):
                self.update_rel = rel
                self._set_update_state('available')
            if manual and not rel:
                msg = t('update_check_failed', error=err) if err else t('update_latest', version=__version__)
                messagebox.showinfo(APP_NAME, msg, parent=self)
        elif kind == 'update_progress':
            if self.update_state == 'downloading':
                self.update_info = data
                self.update_lbl.configure(text=t('update_downloading', pct=data))
        elif kind == 'update_ready':
            self._launch_helper(data)
        elif kind == 'update_failed':
            self._update_failed(data)

    # ------------------------------------------------------------------ updates (see updater.py)
    def _auto_check(self):
        if self.settings.get('auto_update', True):
            self.check_updates()
        self.after(updater.CHECK_EVERY_S * 1000, self._auto_check)

    def check_updates(self, manual=False):
        def work():
            try:
                self.events.put(('update_check', (updater.fetch_latest(), manual, None)))
            except Exception as e:
                log.info('update check failed: %s', e)
                self.events.put(('update_check', (None, manual, str(e)[:160])))
        threading.Thread(target=work, daemon=True, name='update-check').start()

    def _update_failed(self, err):
        # 'blocked': the new exe vanished right after unpacking - an antivirus quarantined it
        blocked = getattr(err, 'code', None) == 'blocked'
        self._set_update_state('blocked' if blocked else 'failed', str(err)[:160])

    def _set_update_state(self, state, info=''):
        self.update_state, self.update_info = state, info
        self._render_update_bar()

    def _render_update_bar(self):
        bar = self.update_bar
        for w in bar.winfo_children():
            w.destroy()
        st, rel = self.update_state, self.update_rel
        if not st:
            bar.pack_forget()
            return
        blocker = updater.self_update_blocker() if rel else None
        text = {
            'available': t('update_available', version=rel.version if rel else ''),
            'downloading': t('update_downloading', pct=self.update_info or 0),
            'installing': t('update_installing'),
            'failed': t('update_failed', error=self.update_info),
            'blocked': t('update_blocked'),
            'rolled_back': t('update_rolled_back'),
            'done': t('update_done', version=__version__),
        }[st]
        if st == 'available' and blocker == 'readonly':
            text += ' ' + t('update_readonly')
        self.update_lbl = ctk.CTkLabel(bar, text=text, anchor='w', wraplength=440, justify='left')
        self.update_lbl.pack(side='left', padx=10, pady=6, fill='x', expand=True)

        def button(text, cmd, accent=False, width=90):
            ctk.CTkButton(bar, text=text, command=cmd, height=28, width=width,
                          fg_color=ACCENT if accent else '#3a4a44', hover_color='#2f8f6d' if accent else '#46594f'
                          ).pack(side='right', padx=(0, 8), pady=6)
        if st == 'done':
            button('✕', lambda: self._set_update_state(None), width=28)
        elif st in ('available', 'failed', 'blocked', 'rolled_back'):
            button(t('update_later'), lambda: self._set_update_state(None))
        if st == 'available' and not blocker:
            button(t('update_whats_new'), lambda: webbrowser.open(rel.page))
            button(t('update_now'), self._start_update, accent=True, width=150)
        elif st in ('available', 'failed', 'blocked', 'rolled_back'):
            page = rel.page if rel else PROJECT_URL + '/releases/latest'
            button(t('update_download'), lambda: webbrowser.open(page), accent=True)
        bar.pack(fill='x', padx=14, pady=(2, 4), after=self._header)

    def _start_update(self):
        rel = self.update_rel
        self._set_update_state('downloading')

        def work():
            try:
                zip_path = updater.download(rel, progress=lambda pct: self.events.put(('update_progress', pct)))
                self.events.put(('update_ready', updater.extract(zip_path, rel)))
            except Exception as e:
                log.exception('update download failed')
                self.events.put(('update_failed', e))
        threading.Thread(target=work, daemon=True, name='update-download').start()

    def _launch_helper(self, app_dir):
        """Start the new version as installer, then close as soon as it is running (it waits for us to exit)."""
        self._set_update_state('installing')
        try:
            proc, ready = updater.launch_helper(app_dir, updater.install_dir())
        except (OSError, updater.UpdateError) as e:
            log.exception('could not start the update')
            self._update_failed(e)
            return
        t0 = time.time()

        def wait():
            if os.path.exists(ready):
                log.info('update helper running, closing for the update')
                self.on_close()
            elif proc.poll() is not None or time.time() - t0 > HELPER_TIMEOUT_S:
                if proc.poll() is None:
                    proc.kill()
                if not os.path.isfile(os.path.join(app_dir, updater.EXE)):
                    self._update_failed(updater.UpdateError('installer removed', code='blocked'))
                else:
                    self._set_update_state('failed', f'installer did not start (exit code {proc.poll()})')
            else:
                self.after(200, wait)
        self.after(200, wait)

    def _show_status(self, data):
        st = data['state']
        color = {'watching': ACCENT, 'paused': '#d0a040', 'no_game': '#888', 'no_ocr': '#e05555'}.get(st, '#888')
        self.status_dot.configure(text_color=color)
        self.status_lbl.configure(text=t('status_' + st, size=data.get('size', '')))

    # ------------------------------------------------------------------ table + stats
    def refresh_table(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        lang = self._lang()
        for c in self.store.recent(300):
            fish = self.gd.fish_name(c['fish_id'], lang) if c['fish_id'] else f"{t('unknown_fish')} ({c['name_text'] or ''})"
            level = self.gd.trophy_level(c['fish_id'], c['weight_g'])
            fish = LEVEL_ICON.get(level, '') + fish
            spot = f"{c['x']}:{c['y']}" if c['x'] is not None else '–'
            cloud = {1: '✓', -1: '✗'}.get(c['uploaded'], '…' if self.settings.get('upload') else '')
            tags = ('super',) if level == 2 else ('trophy',) if level else ('unknown',) if not c['fish_id'] else ()
            self.tree.insert('', 'end', iid=c['uuid'], tags=tags, values=(
                local_time(c['caught_at']), fish, fmt_weight(c['weight_g']),
                f"{c['length_cm']:.0f} {t('cm')}" if c['length_cm'] else '–', spot, cloud))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        self._update_stats()

    def _update_stats(self):
        rows = self.store.since(self.session_start)
        hours = max((time.time() - self.session_t0) / 3600, 1 / 60)
        self.stat_vals['stat_catches'].configure(text=str(len(rows)))
        self.stat_vals['stat_per_hour'].configure(text=f'{len(rows) / hours:.1f}')
        self.stat_vals['stat_trophies'].configure(
            text=str(sum(1 for r in rows if self.gd.trophy_level(r['fish_id'], r['weight_g']))))
        m = int((time.time() - self.session_t0) // 60)
        self.stat_vals['stat_session'].configure(text=f'{m // 60}:{m % 60:02d}')
        if not self.settings.get('upload'):
            txt = t('upload_off')
        else:
            allr = self.store.recent(1000)
            ok = sum(1 for r in allr if r['uploaded'] == 1)
            pend = sum(1 for r in allr if r['uploaded'] == 0)
            rej = sum(1 for r in allr if r['uploaded'] == -1)
            parts = [t('upload_ok', n=ok)]
            if pend:
                parts.append(t('upload_pending', n=pend))
            if rej:
                parts.append(t('upload_rejected', n=rej))
            if self.uploader.outdated:
                parts.append(t('upload_outdated'))
            elif self.uploader.last_error:
                parts.append(t('upload_error'))
            txt = '☁ ' + ' · '.join(parts)
        self.upload_lbl.configure(text=txt)

    def _tick_stats(self):
        self._update_stats()
        self.after(30000, self._tick_stats)

    # ------------------------------------------------------------------ controls
    def _on_water(self, name):
        wid = self.water_ids[self.water_names.index(name)]
        self.settings.set('waterbody', wid)
        self._not_here = None
        self._update_warning()

    def _update_warning(self):
        if self.settings.get('waterbody') not in self.gd.waterbodies:
            self.warn.configure(text=t('waterbody_missing'))
        elif self._not_here:
            fid, water = self._not_here
            self.warn.configure(text=t('fish_not_here', fish=self.gd.fish_name(fid, self._lang()),
                                       water=self.gd.water_name(water, self._lang())))
        else:
            self.warn.pack_forget()
            return
        self.warn.pack(fill='x', padx=14, after=self.status_lbl.master)

    def _on_game_lang(self, label):
        key = next(k for k, v in self.gl_labels.items() if v == label)
        self.settings.set('game_lang', key)

    def toggle_pause(self):
        self.engine.paused = not self.engine.paused
        self.pause_btn.configure(text=t('resume') if self.engine.paused else t('pause'))

    def _selected(self):
        sel = self.tree.selection()
        return self.store.get(sel[0]) if sel else None

    def delete_selected(self):
        c = self._selected()
        if c and messagebox.askyesno(APP_NAME, t('delete_confirm'), parent=self):
            self.store.update(c['uuid'], deleted=1)
            self.refresh_table()
            self.uploader.kick()  # removes it from the community map too

    def edit_selected(self):
        c = self._selected()
        if c:
            EditDialog(self, c)

    def open_settings(self):
        SettingsDialog(self)

    def on_close(self):
        self.settings.set('window', self.geometry())
        self.engine.stop()
        self.uploader.stop()
        self.destroy()


class EditDialog(ctk.CTkToplevel):
    def __init__(self, app, catch):
        super().__init__(app)
        self.app, self.c = app, catch
        self.title(t('edit'))
        self.geometry('420x330')
        self.transient(app)
        lang = app._lang()
        gd = app.gd
        self.fish_ids = sorted(gd.fish, key=lambda f: gd.fish_name(f, lang).lower())
        self.fish_names = [gd.fish_name(f, lang) for f in self.fish_ids]
        frm = ctk.CTkFrame(self, fg_color='transparent')
        frm.pack(fill='both', expand=True, padx=16, pady=14)
        self.vars = {}

        def field(r, label, value, widget=None):
            ctk.CTkLabel(frm, text=label).grid(row=r, column=0, sticky='w', pady=5)
            var = tk.StringVar(value='' if value is None else str(value))
            w = widget(var) if widget else ctk.CTkEntry(frm, textvariable=var, width=230)
            w.grid(row=r, column=1, sticky='we', pady=5, padx=(10, 0))
            return var
        cur_fish = gd.fish_name(catch['fish_id'], lang) if catch['fish_id'] else ''
        self.vars['fish'] = field(0, t('fish'), cur_fish,
                                  lambda v: ctk.CTkComboBox(frm, values=self.fish_names, variable=v, width=230))
        self.vars['weight_g'] = field(1, t('weight_g'), catch['weight_g'])
        self.vars['length_cm'] = field(2, t('length_cm'), catch['length_cm'])
        self.vars['x'] = field(3, t('coord_x'), catch['x'])
        self.vars['y'] = field(4, t('coord_y'), catch['y'])
        wnames = app.water_names
        cur_w = gd.water_name(catch['waterbody'], lang) if catch['waterbody'] else ''
        self.vars['water'] = field(5, t('waterbody'), cur_w,
                                   lambda v: ctk.CTkOptionMenu(frm, values=wnames, variable=v, width=230))
        btns = ctk.CTkFrame(self, fg_color='transparent')
        btns.pack(fill='x', padx=16, pady=(0, 14))
        ctk.CTkButton(btns, text=t('save'), command=self.save).pack(side='right')
        ctk.CTkButton(btns, text=t('cancel'), fg_color='#444', command=self.destroy).pack(side='right', padx=8)
        self.after(100, self.grab_set)

    def save(self):
        v = {k: var.get().strip() for k, var in self.vars.items()}

        def num(s, cast):
            try:
                return cast(s.replace(',', '.')) if s else None
            except ValueError:
                return None
        fish_id = self.fish_ids[self.fish_names.index(v['fish'])] if v['fish'] in self.fish_names else self.c['fish_id']
        water = self.app.water_ids[self.app.water_names.index(v['water'])] if v['water'] in self.app.water_names \
            else self.c['waterbody']
        weight = num(v['weight_g'], int)
        self.app.store.update(self.c['uuid'], fish_id=fish_id, weight_g=weight,
                              length_cm=num(v['length_cm'], float), x=num(v['x'], int), y=num(v['y'], int),
                              waterbody=water, badge=LEVELS[self.app.gd.trophy_level(fish_id, weight)],
                              uploaded=0, upload_note=None)
        self.app.refresh_table()
        self.app.uploader.kick()
        self.destroy()


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        s = app.settings
        self.title(t('settings_title'))
        self.geometry('560x480')
        self.transient(app)
        frm = ctk.CTkFrame(self, fg_color='transparent')
        frm.pack(fill='both', expand=True, padx=18, pady=14)

        self.share = tk.BooleanVar(value=s.get('upload', True))
        ctk.CTkCheckBox(frm, text=t('share'), variable=self.share,
                        command=lambda: (s.set('upload', self.share.get()), app.refresh_table(), app.uploader.kick())
                        ).grid(row=2, column=0, columnspan=2, sticky='w', pady=(14, 2))
        ctk.CTkLabel(frm, text=t('share_hint'), text_color='#9aa4ad', wraplength=500, justify='left'
                     ).grid(row=3, column=0, columnspan=2, sticky='w')
        ctk.CTkLabel(frm, text=t('server_url')).grid(row=4, column=0, sticky='w', pady=6)
        self.url = tk.StringVar(value=s.get('server_url', ''))
        e = ctk.CTkEntry(frm, textvariable=self.url, width=320)
        e.grid(row=4, column=1, sticky='w', padx=10)
        e.bind('<FocusOut>', lambda ev: s.set('server_url', self.url.get().strip()))

        self.bite = tk.BooleanVar(value=s.get('bite_alert', True))
        bf = ctk.CTkFrame(frm, fg_color='transparent')
        bf.grid(row=5, column=0, columnspan=2, sticky='w', pady=(14, 2))
        ctk.CTkCheckBox(bf, text=t('bite_alert'), variable=self.bite,
                        command=lambda: s.set('bite_alert', self.bite.get())).pack(side='left')
        ctk.CTkButton(bf, text=t('test_sound'), width=70, command=lambda: sound.play('bite')).pack(side='left', padx=10)
        vf = ctk.CTkFrame(frm, fg_color='transparent')
        vf.grid(row=6, column=0, columnspan=2, sticky='w', pady=(6, 2))
        ctk.CTkLabel(vf, text=t('alert_volume')).pack(side='left')
        self.vol = ctk.CTkSlider(vf, from_=0, to=100, number_of_steps=20, width=220, command=self._on_volume)
        self.vol.set(round(s.get('alert_volume', 0.5) * 100))
        self.vol.pack(side='left', padx=(10, 6))
        self.vol_lbl = ctk.CTkLabel(vf, text='', width=44)
        self.vol_lbl.pack(side='left')
        # save + play a preview when the slider is let go
        self.vol.bind('<ButtonRelease-1>', lambda e: (s.set('alert_volume', sound.get_volume()), sound.play('bite')))
        self._on_volume(self.vol.get())
        if not app.engine.bite.enabled:
            ctk.CTkLabel(frm, text=t('bite_alert_missing'), text_color='#9aa4ad').grid(row=10, column=0, columnspan=2,
                                                                                       sticky='w')
        self.dbg = tk.BooleanVar(value=s.get('save_debug_images', False))
        ctk.CTkCheckBox(frm, text=t('debug_images'), variable=self.dbg,
                        command=lambda: s.set('save_debug_images', self.dbg.get())
                        ).grid(row=7, column=0, columnspan=2, sticky='w', pady=(14, 2))
        self.auto_upd = tk.BooleanVar(value=s.get('auto_update', True))
        uf = ctk.CTkFrame(frm, fg_color='transparent')
        uf.grid(row=8, column=0, columnspan=2, sticky='w', pady=(14, 2))
        ctk.CTkCheckBox(uf, text=t('auto_update'), variable=self.auto_upd,
                        command=lambda: s.set('auto_update', self.auto_upd.get())).pack(side='left')
        ctk.CTkButton(uf, text=t('check_now'), width=90, command=lambda: app.check_updates(manual=True)
                      ).pack(side='left', padx=10)
        ctk.CTkButton(frm, text=t('open_folder'), command=lambda: os.startfile(USER_DIR)
                      ).grid(row=9, column=0, sticky='w', pady=(18, 0))
        self.protocol('WM_DELETE_WINDOW', self._close)

    def _on_volume(self, value):
        sound.set_volume(float(value) / 100)
        self.vol_lbl.configure(text=f'{int(round(float(value)))} %')

    def _close(self):
        self.app.settings.set('alert_volume', sound.get_volume())
        self.app.settings.set('server_url', self.url.get().strip())
        self.destroy()
