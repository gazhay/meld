# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import logging

from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from meld import misc
from meld.conf import _
from meld.const import TEXT_FILTER_ACTION_FORMAT, ActionMode
from meld.filediff import FileDiff
from meld.melddoc import MeldDoc
from meld.recent import RecentType
from meld.settings import bind_settings, get_meld_settings
from meld.ui.util import make_multiobject_property_action

log = logging.getLogger(__name__)

# These lists contain all the actions in FileDiff, except for the actions generated for
# each filter.
# Those lists are verified by _verify_action_lists()

FWD_TO_ACTIVE_ACTIONS = [
    "add-sync-point",
    "move-sync-point",
    "match-sync-point",
    "remove-sync-point",
    "clear-sync-point",
    "copy",
    "copy-full-path",
    "cut",
    "file-push-left",
    "file-push-right",
    "file-pull-left",
    "file-pull-right",
    "file-copy-left-up",
    "file-copy-right-up",
    "file-copy-left-down",
    "file-copy-right-down",
    "file-delete",
    "find",
    "find-next",
    "find-previous",
    "find-replace",
    "format-as-patch",
    "go-to-line",
    "merge-all-left",
    "merge-all-right",
    "merge-all",
    "next-change",
    "next-change-shortcut",
    "next-pane",
    "open-external",
    "open-folder",
    "paste",
    "previous-change",
    "previous-change-shortcut",
    "previous-pane",
    "redo",
    "undo",
]

FWD_TO_DIFF2_ACTIONS = [
    "file-previous-conflict",
    "file-next-conflict",
]

FWD_TO_ALL_ACTIONS = [
    "refresh",
    "revert",
    "save",
    "save-all",
]

DISABLED_ACTIONS = [
    # save-as is disabled since we don't want filenames to change
    "save-as",
    # swap-2-panes is disabled since reversing all panes would require more work, and I
    # currently don't see why it should be useful.
    "swap-2-panes",
]

PROPERTY_ACTIONS = {
    "show-overview-map": "show-overview-map",
    "lock-scrolling": "lock_scrolling",
}

STATE_ACTIONS = {
    "text-filter": False,
}


def _get_diff_actions(diff: FileDiff) -> tuple[set[str], set[str]]:
    """Get all actions in a FileDiff, stateless and stateful"""
    all_action_names = set(diff.view_action_group.list_actions())
    stateful_action_names = set()
    stateless_action_names = set()
    for action_name in all_action_names:
        state = diff.view_action_group.get_action_state(action_name)
        if state is None:
            stateless_action_names.add(action_name)
        else:
            # Assert all stateful actions have type bool
            assert state.get_type_string() == "b"
            stateful_action_names.add(action_name)
    return stateless_action_names, stateful_action_names


def _verify_action_lists(diff: FileDiff):
    """Assert that the action lists cover all the actions in the FileDiff."""
    stateless_names, stateful_names = _get_diff_actions(diff)
    expected_stateless_names = (
        FWD_TO_ACTIVE_ACTIONS
        + FWD_TO_DIFF2_ACTIONS
        + FWD_TO_ALL_ACTIONS
        + DISABLED_ACTIONS
    )
    assert set(expected_stateless_names) == stateless_names
    # In addition to the listed actions, the FileDiff creates
    # stateful actions for each text filter. We expect those as well.
    n_text_filters = len(get_meld_settings().text_filters)
    text_filter_action_names = [
        TEXT_FILTER_ACTION_FORMAT.format(i) for i in range(n_text_filters)
    ]
    expected_stateful_names = (
        list(PROPERTY_ACTIONS) + list(STATE_ACTIONS) + text_filter_action_names
    )
    assert set(expected_stateful_names) == stateful_names


class ShrinkingBin(Gtk.Widget):
    """
    A widget which reports its preferred width to be the minimum width of its child
    """

    def do_measure(self, orientation, for_size):
        child = self.get_first_child()
        if not child or not child.get_visible():
            return (0, 0, -1, -1)
        min_size, nat_size, min_baseline, nat_baseline = child.measure(
            orientation, for_size
        )
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (min_size, min_size, min_baseline, nat_baseline)
        else:
            return (min_size, nat_size, min_baseline, nat_baseline)

    def do_size_allocate(self, width, height, baseline):
        child = self.get_first_child()
        if child and child.get_visible():
            alloc = Gdk.Rectangle()
            alloc.width = width
            alloc.height = height
            child.size_allocate(alloc, baseline)

    def do_dispose(self):
        child = self.get_first_child()
        if child:
            child.unparent()
        Gtk.Widget.do_dispose(self)


class ExactWidthBin(Gtk.Widget):
    """
    A widget which allocates its exact width request to its child
    """

    def do_measure(self, orientation, for_size):
        child = self.get_first_child()
        if child is None or not child.get_visible():
            return 0, 0, -1, -1

        req_width, _req_height = self.get_size_request()
        if orientation == Gtk.Orientation.VERTICAL and req_width != -1:
            for_size = req_width
        min_size, nat_size, min_baseline, nat_baseline = child.measure(
            orientation, for_size
        )
        if orientation == Gtk.Orientation.HORIZONTAL and req_width != -1:
            return req_width, req_width, -1, -1
        else:
            return min_size, nat_size, min_baseline, nat_baseline

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        child = self.get_first_child()
        if child and child.get_visible():
            req_width, _req_height = self.get_size_request()
            if req_width != -1:
                width = req_width
            alloc = Gdk.Rectangle()
            alloc.width = width
            alloc.height = height
            child.size_allocate(alloc, baseline)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_dispose(self):
        child = self.get_first_child()
        if child:
            child.unparent()
        Gtk.Widget.do_dispose(self)


def _wrap_with_exact_width_bin(widget: Gtk.Widget) -> Gtk.Widget:
    assert widget.get_layout_manager() is None
    box = widget.get_parent()
    assert isinstance(box, Gtk.Box)
    assert widget.get_next_sibling() is None, "Expecting widget to be the last child"
    assert widget.get_vexpand()
    box.remove(widget)
    exact = ExactWidthBin()
    widget.set_parent(exact)
    exact.set_vexpand(True)
    box.append(exact)
    return exact


class TrackedOverlayLayout(Gtk.OverlayLayout):
    """Used below to get notified when FourDiff gets a size allocation"""

    def do_allocate(self, widget, width, height, baseline):
        Gtk.OverlayLayout.do_allocate(self, widget, width, height, baseline)
        widget.on_tracked_size_allocate(width, height, baseline)


class FourDiff(Gtk.Overlay, MeldDoc):
    """
    Four way comparison of text files

    There are 4 files: 0: BASE, 1: REMOTE, 2: LOCAL, 3: RESULT
    Only the RESULT buffer is editable.
    LOCAL has the local file, before applying the diff.
    The user aims to apply the diff between BASE and REMOTE onto LOCAL.
    Or: RESULT - LOCAL = REMOTE - BASE
    Or: RESULT = LOCAL + (REMOTE - BASE)

    Sometimes it's easier to apply the diff between BASE and LOCAL onto REMOTE.
    Or: RESULT = REMOTE + (LOCAL - BASE)
    So it's possible to swap REMOTE and LOCAL.

    The FourDiff doc contains 3 FileDiffs:
    0: BASE-REMOTE  1: BASE-LOCAL  2: LOCAL-RESULT
    There are 2 views. At any time, either:
    1: the BASE-REMOTE and LOCAL-RESULT diffs are displayed, showing the
       source diff and the result diff, or:
    2: the BASE-LOCAL diff is displayed, showing the source of conflicts.

    The two BASE panes scrolling are kept in sync, and so are the two LOCAL
    panes. This causes all panes to be kept in sync.
    """

    __gtype_name__ = "FourDiff"

    close_signal = MeldDoc.close_signal
    create_diff_signal = MeldDoc.create_diff_signal
    file_changed_signal = MeldDoc.file_changed_signal
    tab_state_changed = MeldDoc.tab_state_changed

    __gsettings_bindings_view__ = (
        ("ignore-blank-lines", "ignore-blank-lines"),
        ("show-overview-map", "show-overview-map"),
        ("overview-map-style", "overview-map-style"),
    )

    ignore_blank_lines = GObject.Property(
        type=bool,
        nick="Ignore blank lines",
        blurb="Whether to ignore blank lines when comparing file contents",
        default=False,
    )
    show_overview_map = GObject.Property(type=bool, default=True)
    overview_map_style = GObject.Property(type=str, default="chunkmap")

    tab_title = GObject.Property(type=str, nick="Title used for tab labels")
    tab_tooltip = GObject.Property(type=str, nick="Tooltip used for tab labels")

    action_mode = GObject.Property(
        type=int,
        nick="Action mode for chunk change actions",
        default=ActionMode.Replace,
    )

    lock_scrolling = GObject.Property(
        type=bool,
        nick="Lock scrolling of all panes",
        default=False,
    )

    def __init__(self):
        super().__init__()
        # FIXME:
        # See FileDiff.__init__, which calls this an "unimaginable hack".
        # I don't really understand the issue. It mentions Gtk.Template, which we
        # don't inherit from, so perhaps this could be fixed here.
        MeldDoc.__init__(self)
        bind_settings(self)

        assert isinstance(self.get_layout_manager(), Gtk.OverlayLayout)
        self.set_layout_manager(TrackedOverlayLayout())

        # Init diff0, diff1, and diff2
        self.diff0 = FileDiff(2)
        self.scheduler.add_scheduler(self.diff0.scheduler)
        self.diff0.connect("close", self.on_diff0_close)

        self.diff1 = FileDiff(2)
        self.exact0 = _wrap_with_exact_width_bin(self.diff1.scrolledwindow0)
        self.exact1 = _wrap_with_exact_width_bin(self.diff1.scrolledwindow1)
        self.scheduler.add_scheduler(self.diff1.scheduler)
        self.diff1.connect("close", self.on_diff1_close)

        self.diff2 = FileDiff(2, mark_pane1_conflict_markers=True)
        self.scheduler.add_scheduler(self.diff2.scheduler)
        self.undosequence = self.diff2.undosequence
        self.diff2.connect("close", self.on_diff2_close)

        self.diffs = [self.diff0, self.diff1, self.diff2]

        # We use an Overlay instead of a Stack, because a Stack only layouts a
        # page when it's shown, which causes the views to scroll unpredictably.
        # Instead, we use an Overlay, and control which widget is on top.
        # The widgets in the overlay are:
        # 1. self.hbox, containing diff0 and diff2
        # 2. self.diff1
        # 3. self.cover, which is always below diff1, just to hide hbox.
        self.hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.hbox.set_homogeneous(True)
        for diff in [self.diff0, self.diff2]:
            diff.set_hexpand(True)
            diff.set_halign(Gtk.Align.FILL)
            self.hbox.append(diff)
        self.add_overlay(self.hbox)

        # self.cover is shown beneath diff1, to hide hbox0.
        # The "background" style causes it to be opaque rather than transparent.
        self.cover = Gtk.Box()
        self.cover.get_style_context().add_class("background")
        self.add_overlay(self.cover)

        # We put diff1 inside ShrinkingBin, so it will only get its minimum width
        self.shrinking_bin = ShrinkingBin()
        self.diff1.set_parent(self.shrinking_bin)
        self.shrinking_bin.set_halign(Gtk.Align.START)
        self.add_overlay(self.shrinking_bin)

        # We always have an active FileDiff, which is self.diffs[self.active_diff_i].
        # When Showing 1 FileDiff, it is the active diff. When showing 2 FileDiffs, it's
        # the one which last received focus.
        self.active_diff_i = 2
        self.active_diff = self.diffs[self.active_diff_i]
        # Start with showing 2 diffs
        self.move_to_top(self.hbox)
        self.is_showing_2_diffs = True
        self.active_diff_i_when_showing_2_diffs = 2

        for diff_i in [0, 2]:
            for tv in self.diffs[diff_i].textview:
                focus_controller = Gtk.EventControllerFocus()
                tv.add_controller(focus_controller)
                focus_controller.connect(
                    "enter", self.on_textview_focus_in_event, diff_i
                )

        for diff in self.diffs:
            diff.connect("notify::tab-title", self.on_diff_label_changed)

        meld_settings = get_meld_settings()
        self.settings_handlers = [
            meld_settings.connect("text-filters-changed", self.on_text_filters_changed)
        ]
        self.create_text_filters()
        text_filter_action = Gio.SimpleAction.new_stateful(
            "text-filter", None, GLib.Variant.new_boolean(False)
        )
        self.view_action_group.add_action(text_filter_action)

        self._init_actions()

        # We want to link the local-view properties, since they are already linked
        # between the textviews in a single filediff.
        self._link_view_actions()
        # Meld allows choosing a different highlighting language for each textview.
        # When there are 4, this becomes really cumbersome. So we just link them all,
        # since most of the time the user will want the same language for all.
        self._link_source_language()

        self.files = None

        self.connect_scrolledwindows()

    def _init_actions(self):
        """
        Create actions to forward to the FileDiffs.
        Most actions are forwarded to the active FileDiff, some are forwarded to all.
        """
        for diff in self.diffs:
            _verify_action_lists(diff)

        my_actions = [
            ("fourdiff-toggle-view", self.action_toggle_view),
            ("fourdiff-swap-remote-and-local", self.action_swap_remote_and_local),
        ]
        for name, callback in my_actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.view_action_group.add_action(action)

        for name in FWD_TO_ACTIVE_ACTIONS:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", self.on_fwd_to_active_action_activate)
            self.view_action_group.add_action(action)

        for name in FWD_TO_DIFF2_ACTIONS:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", self.on_fwd_to_diff2_action_activate)
            self.view_action_group.add_action(action)
            self.view_action_group.lookup(name).set_enabled(
                self.diff2.view_action_group.lookup(name).get_enabled()
            )

        for name in FWD_TO_ALL_ACTIONS:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", self.on_fwd_to_all_action_activate)
            self.view_action_group.add_action(action)

        for action_name, prop_name in PROPERTY_ACTIONS.items():
            action = Gio.PropertyAction.new(action_name, self, prop_name)
            action.connect("notify::state", self.on_property_action_change_state)
            self.view_action_group.add_action(action)

        for action_name, state in STATE_ACTIONS.items():
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(state)
            )
            action.connect("activate", self.on_fwd_to_all_action_activate)
            action.connect("change-state", self.on_action_change_state)
            self.view_action_group.add_action(action)

        for diff_i, diff in enumerate(self.diffs):
            diff.view_action_group.connect(
                "action-enabled-changed", self.on_diff_action_enabled_changed, diff_i
            )

        self.toolbar_actions = self.diff2.toolbar_actions
        builder = self.diff2.toolbar_actions_builder
        builder.get_object("fourdiff_swap_remote_and_local_button").set_visible(True)
        builder.get_object("fourdiff_toggle_view_button").set_visible(True)

    def _link_view_actions(self):
        """Link the view-local actions of all 3 filediffs"""
        # This list is from filediff.py
        sourceview_prop_actions = [
            "draw-spaces-bool",
            "highlight-current-line-local",
            "show-line-numbers",
            "wrap-mode-bool",
        ]
        textviews = [textview for diff in self.diffs for textview in diff.textview]
        prop_action_group = Gio.SimpleActionGroup()
        for prop in sourceview_prop_actions:
            action = make_multiobject_property_action(textviews, prop)
            prop_action_group.add_action(action)
        for diff in self.diffs:
            diff.insert_action_group("view-local", prop_action_group)

    def _link_source_language(self):
        """Link the syntax highlighting language of all textviews"""
        bufs = [buf for diff in self.diffs for buf in diff.textbuffer]
        hub, *rest = bufs
        for buf in rest:
            hub.bind_property(
                "language",
                buf,
                "language",
                GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
            )

    def set_diff1_linkmap0_width_request(self):
        # Set diff1.linkmap0 width request so that diff1.scrolledwindow1 will be in the
        # same position as diff2.scrolledwindow0.
        # There is one widget between diff1.linkmap0 and diff1.scrolledwindow1, which is
        # diff1.actiongutter1.
        # So we subtract its width from the requested width of diff1.linkmap0.
        xy_or_none = self.diff2.scrolledwindow0.translate_coordinates(
            self.diff1.linkmap0, 0, 0
        )
        if xy_or_none is None:
            # If the widgets weren't realized yet, do nothing.
            return
        x_dist, _y_dist = xy_or_none
        ag1_width = self.diff1.actiongutter1.get_size_request().width
        req_width = x_dist - ag1_width
        self.diff1.linkmap0.set_size_request(max(req_width, -1), -1)

    def on_tracked_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self.set_diff1_linkmap0_width_request()
        self.exact0.set_size_request(self.diff0.scrolledwindow0.get_width(), -1)
        self.exact1.set_size_request(self.diff2.scrolledwindow0.get_width(), -1)

    def on_fwd_to_active_action_activate(self, action, user_data):
        self.active_diff.view_action_group.activate_action(action.get_name(), user_data)

    def on_fwd_to_diff2_action_activate(self, action, user_data):
        self.diff2.view_action_group.activate_action(action.get_name(), user_data)

    def on_fwd_to_all_action_activate(self, action, user_data):
        for diff in self.diffs:
            diff.view_action_group.activate_action(action.get_name(), user_data)

    def on_diff_action_enabled_changed(self, _action_group, name, enabled, diff_i):
        relevant_diff_i = 2 if name in FWD_TO_DIFF2_ACTIONS else self.active_diff_i
        if diff_i == relevant_diff_i:
            self.view_action_group.lookup(name).set_enabled(enabled)

    def on_property_action_change_state(self, paction, _param_spec):
        for diff in self.diffs:
            diff.view_action_group.change_action_state(
                paction.props.name, paction.props.state
            )

    def on_action_change_state(self, action, state):
        for diff in self.diffs:
            diff.view_action_group.change_action_state(action.get_name(), state)

    def _update_active_diff(self):
        """
        Update self.active_diff_i based on self.active_diff_i_when_showing_2_diffs and
        self.is_showing_2_diffs.
        If changed, send signals and update actions accordingly.
        """
        active_diff_i = (
            self.active_diff_i_when_showing_2_diffs if self.is_showing_2_diffs else 1
        )
        if active_diff_i != self.active_diff_i:
            self.active_diff_i = active_diff_i
            self.active_diff = self.diffs[active_diff_i]

            diff_view_action_group = self.active_diff.view_action_group
            for name in FWD_TO_ACTIVE_ACTIONS:
                self.view_action_group.lookup(name).set_enabled(
                    diff_view_action_group.lookup(name).get_enabled()
                )

    def on_textview_focus_in_event(self, _widget, diff_i):
        self.active_diff_i_when_showing_2_diffs = diff_i
        self._update_active_diff()

    @staticmethod
    def _set_read_only(diff, panes):
        # A helper function for set_files()
        for pane in panes:
            buf = diff.textbuffer[pane]
            buf.data.force_read_only = True
            diff.update_buffer_writable(buf)

    def set_files(self, files):
        """Load the given files"""
        assert len(files) == 4
        self.files = files
        self.diff0.set_files(files[:2])
        self._set_read_only(self.diff0, [0, 1])
        self.diff1.set_files([files[0], files[2]])
        self._set_read_only(self.diff1, [0, 1])
        self.diff2.set_files(files[2:])
        self._set_read_only(self.diff2, [0])

        self.recompute_label()

    def recompute_label(self):
        buffers = self.diff0.textbuffer[:2] + self.diff2.textbuffer[:2]
        filenames = [b.data.label for b in buffers]
        shortnames = misc.shorten_names(*filenames)

        if buffers[3].get_modified():
            shortnames[3] += "*"

        label_text = " — ".join(shortnames)
        tooltip_names = filenames
        tooltip_text = "\n".join((_("File comparison:"), *tooltip_names))
        self.tab_title = label_text
        self.tab_tooltip = GLib.markup_escape_text(tooltip_text)

    def on_diff_label_changed(self, *_args):
        self.recompute_label()

    def get_comparison(self):
        buffers = self.diff0.textbuffer[:2] + self.diff2.textbuffer[:2]
        uris = [b.data.gfile for b in buffers]
        return RecentType.FourDiff, uris

    @staticmethod
    def _on_adj_changed(me: Gtk.Adjustment, other: Gtk.Adjustment):
        """Adjust `other` adjustment when `me` was changed."""
        if me.get_upper() != other.get_upper():
            # We are during a change, for example due to changing window size or
            # word-wrap mode. There's no point in syncing in this case.
            return
        v = me.get_value()
        if other.get_value() != v:
            other.set_value(v)

    def connect_scrolledwindows(self):
        sw_pairs = [
            (self.diff0.scrolledwindow[0], self.diff1.scrolledwindow[0]),
            (self.diff1.scrolledwindow[1], self.diff2.scrolledwindow[0]),
        ]
        for sw0, sw1 in sw_pairs:
            for method in [
                Gtk.ScrolledWindow.get_vadjustment,
                Gtk.ScrolledWindow.get_hadjustment,
            ]:
                adj0 = method(sw0)
                adj1 = method(sw1)
                for event in ["value-changed", "changed"]:
                    adj0.connect(event, self._on_adj_changed, adj1)
                    adj1.connect(event, self._on_adj_changed, adj0)

    def move_to_top(self, widget):
        """Move widget to the top of the overlay, putting self.cover beneath it"""
        self.cover.insert_before(self, None)
        widget.insert_before(self, None)

    @staticmethod
    def _copy_cursor(src_view: Gtk.TextView, dst_view: Gtk.TextView) -> None:
        """
        Copy the cursor and selection from src_view to dst_view.
        Also, if src_view has focus, move the focus to dst_view.
        """
        src, dst = src_view.get_buffer(), dst_view.get_buffer()
        insert_offset = src.get_iter_at_mark(src.get_insert()).get_offset()
        selbound_offset = src.get_iter_at_mark(src.get_selection_bound()).get_offset()
        new_insert = dst.get_iter_at_offset(insert_offset)
        new_selbound = dst.get_iter_at_offset(selbound_offset)
        dst.select_range(new_insert, new_selbound)
        if src_view.is_focus():
            dst_view.grab_focus()

    def action_toggle_view(self, _action, _value):
        self.is_showing_2_diffs = not self.is_showing_2_diffs
        if self.is_showing_2_diffs:
            self._copy_cursor(self.diff1.textview0, self.diff0.textview0)
            self._copy_cursor(self.diff1.textview1, self.diff2.textview0)
            self.move_to_top(self.hbox)
        else:
            self._copy_cursor(self.diff0.textview0, self.diff1.textview0)
            self._copy_cursor(self.diff2.textview0, self.diff1.textview1)
            self.move_to_top(self.shrinking_bin)
        self._update_active_diff()

    def action_swap_remote_and_local(self, _action, _value):
        assert self.files is not None
        _base, remote, local, _result = self.files
        self.files[1] = local
        self.files[2] = remote
        self.diff0.set_file(1, local)
        self.diff1.set_file(1, remote)
        self.diff2.set_file(0, remote)

        self.recompute_label()

    def get_filter_visibility(self) -> tuple[bool, bool, bool]:
        # The same as FileDiff
        return True, False, False

    def get_conflict_visibility(self) -> bool:
        return True

    def on_text_filters_changed(self, app):
        self.create_text_filters()

    def _update_text_filter(self, action, state):
        for diff in self.diffs:
            diff.view_action_group.change_action_state(action.get_name(), state)
        action.set_state(state)

    def create_text_filters(self):
        # Based on FileDiff.create_text_filters()
        meld_settings = get_meld_settings()
        for i, filt in enumerate(meld_settings.text_filters):
            action = Gio.SimpleAction.new_stateful(
                name=TEXT_FILTER_ACTION_FORMAT.format(i),
                parameter_type=None,
                state=GLib.Variant.new_boolean(filt.active),
            )
            action.connect("change-state", self._update_text_filter)
            action.set_enabled(filt.filter is not None)
            self.view_action_group.add_action(action)

    def request_close(self):
        if not self.diff2.linediffer.conflicts:
            self.request_close_cb(None, "close", None)
            return

        builder = Gtk.Builder.new_from_resource(
            "/org/gnome/meld/ui/close-with-conflicts-confirm-dialog.ui"
        )
        dialog = builder.get_object("close-with-conflicts-confirm-dialog")
        dialog.choose(self.get_root(), None, self.request_close_cb, None)

    def request_close_cb(self, dialog, response, user_data):
        assert user_data is None
        if not isinstance(response, str):
            response = dialog.choose_finish(response)

        if response == "cancel":
            return

        assert response == "close"

        # We start with diff 2, since it contains the editable buffer and
        # the user may decide to abort closing.
        # This will call diff0.request_close(), which will call diff1.request_close(),
        # which will call self.on_diff1_close().
        self.diff2.request_close()

    def on_diff2_close(self, _widget, _exit_code):
        self.diff0.request_close()

    def on_diff0_close(self, _widget, _exit_code):
        self.diff1.request_close()

    def on_diff1_close(self, _widget, _exit_code):
        super().request_close()
