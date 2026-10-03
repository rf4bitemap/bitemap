"""Main window (customtkinter)."""
import os
import queue
import time
import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, ttk

import customtkinter as ctk

from . import APP_NAME, __version__, sound
from .i18n import LANGS, t
from .paths import USER_DIR

GAME_LANGS = ('auto', 'en', 'de', 'ru')
ACCENT = '#3fb68b'


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
            self.refresh_table()
            self.uploader.kick()
            self.after(3000, self.refresh_table)
        elif kind == 'uploaded':
            self.refresh_table()
        elif kind == 'error':
            self.status_lbl.configure(text=str(data)[:120])

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
            if c['badge'] == 'trophy':
                fish = '🏆 ' + fish
            spot = f"{c['x']}:{c['y']}" if c['x'] is not None else '–'
            cloud = {1: '✓', -1: '✗'}.get(c['uploaded'], '…' if self.settings.get('upload') else '')
            tags = ('trophy',) if c['badge'] == 'trophy' else ('unknown',) if not c['fish_id'] else ()
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
        self.stat_vals['stat_trophies'].configure(text=str(sum(1 for r in rows if r['badge'] == 'trophy')))
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
            if self.uploader.last_error:
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
        self._update_warning()

    def _update_warning(self):
        if self.settings.get('waterbody') in self.gd.waterbodies:
            self.warn.pack_forget()
        else:
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
        self.app.store.update(self.c['uuid'], fish_id=fish_id, weight_g=num(v['weight_g'], int),
                              length_cm=num(v['length_cm'], float), x=num(v['x'], int), y=num(v['y'], int),
                              waterbody=water, uploaded=0, upload_note=None)
        self.app.refresh_table()
        self.app.uploader.kick()
        self.destroy()


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        s = app.settings
        self.title(t('settings_title'))
        self.geometry('560x430')
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
        ctk.CTkButton(frm, text=t('open_folder'), command=lambda: os.startfile(USER_DIR)
                      ).grid(row=8, column=0, sticky='w', pady=(18, 0))
        self.protocol('WM_DELETE_WINDOW', self._close)

    def _on_volume(self, value):
        sound.set_volume(float(value) / 100)
        self.vol_lbl.configure(text=f'{int(round(float(value)))} %')

    def _close(self):
        self.app.settings.set('alert_volume', sound.get_volume())
        self.app.settings.set('server_url', self.url.get().strip())
        self.destroy()
