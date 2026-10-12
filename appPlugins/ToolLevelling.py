# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/12/2020                                     #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import Qt
from appTool import AppTool
from defaults import AppDefaults
from appGUI.GUIElements import (
    VerticalScrollArea,
    FCLabel,
    FCButton,
    FCFrame,
    GLay,
    FCComboBox,
    FCCheckBox,
    FCJog,
    RadioSet,
    FCDoubleSpinner,
    FCSpinner,
    FCFileSaveDialog,
    FCDetachableTab,
    FCTable,
    FCZeroAxes,
    FCSliderWithDoubleSpinner,
    FCEntry,
    RotatedToolButton,
)

import logging
import math
from copy import deepcopy
import sys

from shapely import Point, MultiPoint, MultiPolygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.affinity import translate
from datetime import datetime as dt

import gettext
import appTranslation as fcTranslate
import builtins

from appObjects.AppObjectTemplate import ObjectDeleted

from appGUI.VisPyVisuals import *
from appGUI.PlotCanvasLegacy import ShapeCollectionLegacy

from appEditors.appTextEditor import AppTextEditor

from camlib import CNCjob

from appPlugins.levelling_interp import (
    build_bilinear_grid,
    bilinear_offset,
    nearest_offset,
    parse_grbl_probe_output,
    parse_grbl_active_wcs,
    parse_grbl_work_offset,
    match_nearest_point,
    match_tolerance,
    new_levelling_state,
    level_gcode_line,
    parse_height_map_line,
    grbl_reply_error,
    probe_timeout_s,
    grbl_report_scale,
    parse_grbl_setting,
    parse_grbl_status_state,
    grbl_modes_confirmed,
)

import time
import threading
import serial
import glob
import random
from io import StringIO

from matplotlib.backend_bases import KeyEvent as mpl_key_event

# try:
#     from foronoi import Voronoi
#     from foronoi import Polygon as voronoi_poly
#     VORONOI_ENABLED = True
# except Exception:
#     try:
#         from shapely.ops import voronoi_diagram
#         VORONOI_ENABLED = True
#         # from appCommon.Common import voronoi_diagram
#     except Exception:
#         VORONOI_ENABLED = False
try:
    from shapely.ops import voronoi_diagram
    VORONOI_ENABLED = True
    # from appCommon.Common import voronoi_diagram
except Exception:
    VORONOI_ENABLED = False

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class _GrblOperation:
    """
    1 operation that sends commands to GRBL and reads the replies. While it
    exists, it owns the serial port: no other operation can start.

    :param name:   short name for messages, for example 'probe' or 'jog'.
    :param motion: True if the operation can move the machine; it then needs
                   a machine that is not in recovery.
    """

    def __init__(self, name, motion=False):
        self.name = name
        self.motion = motion
        self.cancel = threading.Event()


class ToolLevelling(CNCjob, AppTool):
    # emitted after the serial port closed (also from a worker thread)
    grbl_port_closed_sig = QtCore.pyqtSignal()
    build_al_table_sig = QtCore.pyqtSignal()
    apply_autolevel_sig = QtCore.pyqtSignal()
    show_probing_geo_sig = QtCore.pyqtSignal(bool, bool)

    def __init__(self, app):
        self.app = app
        self.decimals = self.app.decimals

        AppTool.__init__(self, app)
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        CNCjob.__init__(
            self,
            steps_per_circle=self.app.options.get(
                "cncjob_steps_per_circle",
                app_defaults.get("cncjob_steps_per_circle", AppDefaults.factory_defaults.get("cncjob_steps_per_circle"))),
            app=app
        )

        # updated in the self.set_tool_ui()
        self.form_fields = {}

        self.first_click = False
        self.cursor_pos = None

        # if mouse is dragging set the object True
        self.mouse_is_dragging = False

        # if mouse events are bound to local methods
        self.mouse_events_connected = False

        # event handlers references
        self.kp = None
        self.mm = None
        self.mr = None

        self.probing_gcode_text = ''
        self.grbl_probe_result = ''
        self.grbl_work_offset = (0.0, 0.0, 0.0)

        # True only right after a successful probing pass (parse_grbl_probe_result())
        # or a successful height-map import; False whenever probe points are
        # (re)created or cleared, since their 'height' is then just a placeholder.
        self.al_heights_valid = False

        # busy guard for apply_autolevel() (creates the new '_levelled' CNCJob object)
        self._al_apply_running = False

        # serial session: 1 operation that reads GRBL replies at a time
        self._grbl_op_lock = threading.Lock()
        self._grbl_op = None
        # None, 'needs_reset' or 'needs_home'; motion is blocked until it is None again
        self._grbl_recovery = None
        # True if a disconnect waits for the running operation to stop
        self._grbl_close_pending = False

        '''
        dictionary of dictionaries to store the information's for the autolevelling
        format when using Voronoi diagram:
        {
            id: {
                    'point': Shapely Point
                    'geo': Shapely Polygon from Voronoi diagram,
                    'height': float
                }
        }
        '''
        self.al_voronoi_geo_storage = {}

        '''
        list of (x, y, x) tuples to store the information's for the autolevelling
        format when using bilinear interpolation:
        [(x0, y0, z0), (x1, y1, z1), ...]
        '''
        self.al_bilinear_geo_storage = []

        # working state used by autolevell_gcode() / autolevell_gcode_line()
        # while levelling a CNCJob's G-code: the (x, y, height) points used
        # for interpolation, the bilinear grid built out of them (if any),
        # the offset function currently selected, and the coordinate
        # decimals used to format the new Z values
        self._al_points = []
        self._al_grid = None
        self._al_offset_fn = None
        self._al_decimals = 4

        self.solid_geo = None
        self.grbl_ser_port = None

        self.probing_shapes = None

        self.gcode_viewer_tab = None

        # store the current selection shape status to be restored after manual adding test points
        self.old_selection_state = self.app.options.get(
            'global_selection_shape',
            app_defaults.get('global_selection_shape', AppDefaults.factory_defaults.get('global_selection_shape'))
        )

        # #############################################################################
        # ######################### Tool GUI ##########################################
        # #############################################################################
        self.ui = LevelUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName

        self.connect_signals_at_init()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='', **kwargs)

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolLevelling()")

        if toggle:
            # if the splitter is hidden, display it
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

            # if the Tool Tab is hidden display it, else hide it but only if the objectName is the same
            found_idx = None
            for idx in range(self.app.ui.notebook.count()):
                if self.app.ui.notebook.widget(idx).objectName() == "plugin_tab":
                    found_idx = idx
                    break
            # show the Tab
            if not found_idx:
                try:
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                except RuntimeError:
                    self.app.ui.plugin_tab = QtWidgets.QWidget()
                    self.app.ui.plugin_tab.setObjectName("plugin_tab")
                    self.app.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.app.ui.plugin_tab)
                    self.app.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)

                    self.app.ui.plugin_scroll_area = VerticalScrollArea()
                    self.app.ui.plugin_tab_layout.addWidget(self.app.ui.plugin_scroll_area)
                    self.app.ui.notebook.addTab(self.app.ui.plugin_tab, _("Plugin"))
                # focus on Tool Tab
                self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)

            try:
                if self.app.ui.plugin_scroll_area.widget().objectName() == self.pluginName and found_idx:
                    # if the Tool Tab is not focused, focus on it
                    if not self.app.ui.notebook.currentWidget() is self.app.ui.plugin_tab:
                        # focus on Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.plugin_tab)
                    else:
                        # else remove the Tool Tab
                        self.app.ui.notebook.setCurrentWidget(self.app.ui.properties_tab)
                        self.app.ui.notebook.removeTab(2)

                        # if there are no objects loaded in the app then hide the Notebook widget
                        if not self.app.collection.get_list():
                            self.app.ui.splitter.setSizes([0, 1])
            except AttributeError:
                pass
        else:
            if self.app.ui.splitter.sizes()[0] == 0:
                self.app.ui.splitter.setSizes([1, 1])

        super().run()
        self.set_tool_ui()

        self.app.ui.notebook.setTabText(2, _("Levelling"))

    def connect_signals_at_init(self):
        self.grbl_port_closed_sig.connect(self._on_grbl_port_closed)
        self.build_al_table_sig.connect(self.build_al_table)
        self.apply_autolevel_sig.connect(self.apply_autolevel)
        self.show_probing_geo_sig.connect(self.show_probing_geo)
        self.ui.level.toggled.connect(self.on_level_changed)

        self.ui.avoid_exc_holes_cb.toggled.connect(self.on_avoid_exc_holes)

        self.ui.al_mode_radio.activated_custom.connect(self.on_mode_radio)
        self.ui.al_method_radio.activated_custom.connect(self.on_method_radio)
        self.ui.al_controller_combo.currentIndexChanged.connect(self.on_controller_change)
        self.ui.plot_probing_pts_cb.toggled.connect(self.show_probing_geo)
        # GRBL
        self.ui.com_search_button.clicked.connect(self.on_grbl_search_ports)
        self.ui.add_bd_button.clicked.connect(self.on_grbl_add_baudrate)
        self.ui.del_bd_button.clicked.connect(self.on_grbl_delete_baudrate_grbl)
        self.ui.controller_reset_button.clicked.connect(self.on_grbl_reset)
        self.ui.com_connect_button.clicked.connect(self.on_grbl_connect)
        self.ui.grbl_send_button.clicked.connect(self.on_grbl_send_command)
        self.ui.grbl_command_entry.returnPressed.connect(self.on_grbl_send_command)

        # Jog
        self.ui.jog_wdg.jog_up_button.clicked.connect(lambda: self.on_grbl_jog(direction='yplus'))
        self.ui.jog_wdg.jog_down_button.clicked.connect(lambda: self.on_grbl_jog(direction='yminus'))
        self.ui.jog_wdg.jog_right_button.clicked.connect(lambda: self.on_grbl_jog(direction='xplus'))
        self.ui.jog_wdg.jog_left_button.clicked.connect(lambda: self.on_grbl_jog(direction='xminus'))
        self.ui.jog_wdg.jog_z_up_button.clicked.connect(lambda: self.on_grbl_jog(direction='zplus'))
        self.ui.jog_wdg.jog_z_down_button.clicked.connect(lambda: self.on_grbl_jog(direction='zminus'))
        self.ui.jog_wdg.jog_origin_button.clicked.connect(lambda: self.on_grbl_jog(direction='origin'))

        # Zero
        self.ui.zero_axs_wdg.grbl_zerox_button.clicked.connect(lambda: self.on_grbl_zero(axis='x'))
        self.ui.zero_axs_wdg.grbl_zeroy_button.clicked.connect(lambda: self.on_grbl_zero(axis='y'))
        self.ui.zero_axs_wdg.grbl_zeroz_button.clicked.connect(lambda: self.on_grbl_zero(axis='z'))
        self.ui.zero_axs_wdg.grbl_zero_all_button.clicked.connect(lambda: self.on_grbl_zero(axis='all'))
        self.ui.zero_axs_wdg.grbl_homing_button.clicked.connect(self.on_grbl_homing)

        # Sender
        self.ui.grbl_report_button.clicked.connect(lambda: self.send_grbl_command(command='?'))
        self.ui.grbl_get_param_button.clicked.connect(
            lambda: self.on_grbl_get_parameter(param=self.ui.grbl_parameter_entry.get_value()))
        self.ui.view_h_gcode_button.clicked.connect(self.on_edit_probing_gcode)
        self.ui.h_gcode_button.clicked.connect(self.on_save_probing_gcode)
        self.ui.import_heights_button.clicked.connect(self.on_import_height_map)
        self.ui.pause_resume_button.clicked.connect(self.on_grbl_pause_resume)
        self.ui.grbl_get_heightmap_button.clicked.connect(self.on_grbl_autolevel)
        self.ui.stop_probing_button.clicked.connect(self.on_grbl_stop)
        self.ui.grbl_save_height_map_button.clicked.connect(self.on_grbl_heightmap_save)

        # When object selection on canvas change
        self.app.proj_selection_changed.connect(self.on_object_selection_changed)

        # Reset Tool
        self.ui.reset_button.clicked.connect(self.set_tool_ui)
        # Cleanup on Graceful exit (CTRL+ALT+X combo key)
        self.app.cleanup.connect(self.set_tool_ui)

    def set_tool_ui(self):
        self.units = self.app.app_units.upper()
        self.al_heights_valid = self._height_map_is_complete()
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        self.clear_ui(self.layout)
        self.ui = LevelUI(layout=self.layout, app=self.app)
        self.pluginName = self.ui.pluginName
        self.connect_signals_at_init()

        # try to select in the CNCJob combobox the active object
        try:
            selected_obj = self.app.collection.get_active()
            if selected_obj.kind == 'cncjob':
                current_name = selected_obj.obj_options['name']
                self.ui.object_combo.set_value(current_name)
        except Exception:
            pass

        loaded_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        if loaded_obj and loaded_obj.kind == 'cncjob':
            name = loaded_obj.obj_options['name']
        else:
            name = ''

        # Shapes container for the Voronoi cells in Autolevelling
        if self.app.use_3d_engine:
            self.probing_shapes = ShapeCollection(
                parent=self.app.plotcanvas.view.scene,
                layers=1,
                pool=self.app.pool
            )
        else:
            self.probing_shapes = ShapeCollectionLegacy(
                obj=self,
                app=self.app,
                name=name + "_probing_shapes"
            )

        self.form_fields.update({
            "tools_al_probe_tip_dia":           self.ui.probe_tip_dia_entry,
            "tools_al_travel_z":                self.ui.ptravelz_entry,
            "tools_al_probe_depth":             self.ui.pdepth_entry,
            "tools_al_probe_fr":                self.ui.feedrate_probe_entry,
            "tools_al_controller":              self.ui.al_controller_combo,
            "tools_al_method":                  self.ui.al_method_radio,
            "tools_al_mode":                    self.ui.al_mode_radio,
            "tools_al_avoid_exc_holes_size":    self.ui.avoid_exc_holes_size_entry,
            "tools_al_rows":                    self.ui.al_rows_entry,
            "tools_al_columns":                 self.ui.al_columns_entry,
            "tools_al_grbl_jog_step":           self.ui.jog_step_entry,
            "tools_al_grbl_jog_fr":             self.ui.jog_fr_entry,
        })

        # Fill Form fields
        self.to_form()
        self.on_controller_change_alter_ui()

        self.ui.plot_probing_pts_cb.set_value(
            self.app.options.get(
                "tools_al_plot_points",
                app_defaults.get("tools_al_plot_points", AppDefaults.factory_defaults.get("tools_al_plot_points")))
        )
        self.ui.avoid_exc_holes_cb.set_value(
            self.app.options.get(
                "tools_al_avoid_exc_holes",
                app_defaults.get("tools_al_avoid_exc_holes", AppDefaults.factory_defaults.get("tools_al_avoid_exc_holes")))
        )

        self.ui.al_probe_points_table.setRowCount(0)
        self.ui.al_probe_points_table.resizeColumnsToContents()
        self.ui.al_probe_points_table.resizeRowsToContents()
        v_header = self.ui.al_probe_points_table.verticalHeader()
        v_header.hide()
        self.ui.al_probe_points_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        h_header = self.ui.al_probe_points_table.horizontalHeader()
        h_header.setMinimumSectionSize(10)
        h_header.setDefaultSectionSize(70)
        h_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        h_header.resizeSection(0, 20)
        h_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.ui.al_probe_points_table.setMinimumHeight(self.ui.al_probe_points_table.getHeight())
        self.ui.al_probe_points_table.setMaximumHeight(self.ui.al_probe_points_table.getHeight())

        # Set initial UI
        self.ui.al_rows_entry.setDisabled(True)
        self.ui.al_rows_label.setDisabled(True)
        self.ui.al_columns_entry.setDisabled(True)
        self.ui.al_columns_label.setDisabled(True)
        self.ui.al_method_lbl.setDisabled(True)
        self.ui.al_method_radio.set_value('v')
        self.ui.al_method_radio.setDisabled(True)

        # Show/Hide Advanced Options
        app_mode = self.app.options.get(
            "global_app_level",
            app_defaults.get("global_app_level", AppDefaults.factory_defaults.get("global_app_level")))
        self.change_level(app_mode)

        try:
            self.ui.object_combo.currentIndexChanged.disconnect()
        except (AttributeError, TypeError):
            pass
        self.ui.object_combo.currentIndexChanged.connect(self.on_object_changed)

        self.build_tool_ui()

        if self._is_supported_al_target(loaded_obj):
            self.ui.al_frame.setDisabled(False)
            # block signals so set_value() cannot trigger the signal-connected
            # on_mode_radio() handler, which would clear a retained height map
            self.ui.al_mode_radio.blockSignals(True)
            self.ui.al_mode_radio.set_value(loaded_obj.obj_options['tools_al_mode'])
            self.ui.al_mode_radio.blockSignals(False)
            self.on_controller_change()

            self._update_mode_radio_ui(val=loaded_obj.obj_options['tools_al_mode'])
            self.on_method_radio(val=loaded_obj.obj_options['tools_al_method'])
        else:
            self.ui.al_frame.setDisabled(True)

        # a retained (probed/imported) height map was preserved above (its
        # validity was already computed at the top of this method); rebuild
        # the table so the kept points show up again
        if self.al_heights_valid:
            self.build_al_table()

        self.on_avoid_exc_holes(
            self.app.options.get(
                "tools_al_avoid_exc_holes",
                app_defaults.get("tools_al_avoid_exc_holes", AppDefaults.factory_defaults.get("tools_al_avoid_exc_holes")))
        )

    def on_object_changed(self):

        # load the object
        obj_name = self.ui.object_combo.currentText()

        # Get source object.
        try:
            target_obj = self.app.collection.get_by_name(obj_name)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Could not retrieve object"), str(obj_name)))
            return

        if self._is_supported_al_target(target_obj):

            self.ui.al_frame.setDisabled(False)

            # Shapes container for the Voronoi cells in Autolevelling
            if self.app.use_3d_engine:
                self.probing_shapes = ShapeCollection(
                    parent=self.app.plotcanvas.view.scene,
                    layers=1,
                    pool=self.app.pool
                )
            else:
                self.probing_shapes = ShapeCollectionLegacy(
                    obj=self,
                    app=self.app,
                    name=obj_name + "_probing_shapes"
                )
        else:
            self.ui.al_frame.setDisabled(True)

    @staticmethod
    def _is_supported_al_target(target_obj):
        return (
                target_obj is not None and
                target_obj.is_segmented_gcode and
                target_obj.obj_options["type"] in ('Geometry', 'Excellon')
        )

    def _height_map_is_complete(self):
        return bool(self.al_voronoi_geo_storage) and all(
            value.get('height') is not None for value in self.al_voronoi_geo_storage.values()
        )

    def on_object_selection_changed(self, current, previous):
        found_idx = None
        for tab_idx in range(self.app.ui.notebook.count()):
            if self.app.ui.notebook.tabText(tab_idx) == self.ui.pluginName:
                found_idx = True
                break

        if found_idx:
            try:
                sel_obj = current.indexes()[0].internalPointer().obj
                name = sel_obj.obj_options['name']
                kind = sel_obj.kind

                if kind == 'cncjob':
                    self.ui.object_combo.set_value(name)
            except IndexError:
                pass

    def change_level(self, level):
        """

        :param level:   application level: either 'b' or 'a'
        :type level:    str
        :return:
        """

        if level == 'a':
            self.ui.level.setChecked(True)
        else:
            self.ui.level.setChecked(False)
        self.on_level_changed(self.ui.level.isChecked())

    def on_level_changed(self, checked):

        # target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())

        # if 'Roland' in target_obj.pp_excellon_name or 'Roland' in target_obj.pp_geometry_name or 'hpgl' in \
        #         target_obj.pp_geometry_name:
        #     # TODO DO NOT AUTOLEVELL
        #     pass

        if not checked:
            self.ui.level.setText('%s' % _('Beginner'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: green;
                                        }
                                        """)

            self.ui.al_title.hide()
            self.ui.show_al_table.hide()
            self.ui.al_probe_points_table.hide()

            # Context Menu section
            # self.ui.al_probe_points_table.removeContextMenu()
        else:
            self.ui.level.setText('%s' % _('Advanced'))
            self.ui.level.setStyleSheet("""
                                        QToolButton
                                        {
                                            color: red;
                                        }
                                        """)

            self.ui.al_title.show()
            self.ui.show_al_table.show()
            if self.ui.show_al_table.get_value():
                self.ui.al_probe_points_table.show()

            # Context Menu section
            # self.ui.al_probe_points_table.setupContextMenu()

    def build_tool_ui(self):
        self.ui_disconnect()

        self.build_al_table()

        self.ui_connect()

    def build_al_table(self):
        tool_idx = 0

        n = len(self.al_voronoi_geo_storage)
        self.ui.al_probe_points_table.setRowCount(n)

        for id_key, value in self.al_voronoi_geo_storage.items():
            tool_idx += 1
            row_no = tool_idx - 1

            t_id = QtWidgets.QTableWidgetItem('%d' % int(tool_idx))
            x = value['point'].x
            y = value['point'].y
            xy_coords = self.app.dec_format(x, dec=self.app.decimals), self.app.dec_format(y, dec=self.app.decimals)
            coords_item = QtWidgets.QTableWidgetItem(str(xy_coords))
            height = self.app.dec_format(value['height'], dec=self.app.decimals)
            height_item = QtWidgets.QTableWidgetItem(str(height))

            t_id.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            coords_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            height_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)

            self.ui.al_probe_points_table.setItem(row_no, 0, t_id)  # Tool name/id
            self.ui.al_probe_points_table.setItem(row_no, 1, coords_item)  # X-Y coords
            self.ui.al_probe_points_table.setItem(row_no, 2, height_item)  # Determined Height

        self.ui.al_probe_points_table.resizeColumnsToContents()
        self.ui.al_probe_points_table.resizeRowsToContents()

        h_header = self.ui.al_probe_points_table.horizontalHeader()
        h_header.setMinimumSectionSize(10)
        h_header.setDefaultSectionSize(70)
        h_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        h_header.resizeSection(0, 20)
        h_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.ui.al_probe_points_table.setMinimumHeight(self.ui.al_probe_points_table.getHeight())
        self.ui.al_probe_points_table.setMaximumHeight(self.ui.al_probe_points_table.getHeight())

        if self.ui.al_probe_points_table.model().rowCount():
            self.ui.grbl_get_heightmap_button.setDisabled(False)
            self.ui.grbl_save_height_map_button.setDisabled(False)
            self.ui.h_gcode_button.setDisabled(False)
            self.ui.view_h_gcode_button.setDisabled(False)
        else:
            self.ui.grbl_get_heightmap_button.setDisabled(True)
            self.ui.grbl_save_height_map_button.setDisabled(True)
            self.ui.h_gcode_button.setDisabled(True)
            self.ui.view_h_gcode_button.setDisabled(True)

    def to_form(self, storage=None):
        if storage is None:
            storage = self.app.options

        for k in self.form_fields:
            for option in storage:
                if option.startswith('tools_al_'):
                    if k == option:
                        try:
                            self.form_fields[k].set_value(storage[option])
                        except Exception:
                            # it may fail for form fields found in the tools tables if there are no rows
                            pass

    def on_add_al_probepoints(self):
        # create the solid_geo

        loaded_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        if loaded_obj is None:
            self.app.log.error("ToolLevelling.on_add_al_probepoints() -> No object loaded.")
            return 'fail'

        try:
            self.solid_geo = unary_union([geo['geom'] for geo in loaded_obj.gcode_parsed if geo['kind'][0] == 'C'])
        except TypeError:
            return 'fail'

        # reset al table
        self.ui.al_probe_points_table.setRowCount(0)

        # reset the al dict
        self.al_voronoi_geo_storage.clear()
        self.al_heights_valid = False

        if self.ui.al_mode_radio.get_value() == 'grid':
            self.on_add_grid_points()
        else:
            self.on_add_manual_points()

    def check_point_over_excellon(self, pol: Polygon, check: bool) -> BaseGeometry:
        if not check:
            return MultiPolygon()

        fused_geometries: list[Polygon] = [
            exc_geo
            for obj_in_collection in self.app.collection.get_list()
            if obj_in_collection.kind == 'excellon' and obj_in_collection.obj_options['plot']
            for exc_geo in MultiPolygon(obj_in_collection.solid_geometry).geoms
            if isinstance(exc_geo, Polygon) and pol.intersects(exc_geo)
        ]
        return unary_union(fused_geometries)

    def on_add_grid_points(self):
        self.al_heights_valid = False

        check_overlap = self.ui.avoid_exc_holes_cb.get_value()
        avoid_step = self.ui.avoid_exc_holes_size_entry.get_value()
        radius = self.ui.probe_tip_dia_entry.get_value() / 2

        xmin, ymin, xmax, ymax = self.solid_geo.bounds

        width = abs(xmax - xmin)
        height = abs(ymax - ymin)
        cols = self.ui.al_columns_entry.get_value()
        rows = self.ui.al_rows_entry.get_value()

        dx = 0 if cols == 1 else width / (cols - 1)
        dy = 0 if rows == 1 else height / (rows - 1)

        points = []
        new_y = ymin
        for x in range(rows):
            new_x = xmin
            for y in range(cols):
                formatted_point = (
                    self.app.dec_format(new_x, self.app.decimals),
                    self.app.dec_format(new_y, self.app.decimals)
                )

                point_buffered = Point(formatted_point).buffer(radius)
                if self.check_point_over_excellon(pol=point_buffered, check=check_overlap).is_empty:
                    # do not add the point if is already added
                    if formatted_point not in points:
                        points.append(formatted_point)
                        new_x += dx
                        continue

                box_poly: Polygon = box(
                    new_x - dx if (new_x - dx) > xmin else xmin,
                    new_y - dy if (new_y - dy) > ymin else ymin,
                    new_x + dx if (new_x + dx) < xmax else xmax,
                    new_y + dy if (new_y + dy) < ymax else ymax
                )

                increments = [
                    (avoid_step, 0),
                    (avoid_step * -1, 0),
                    (0, avoid_step),
                    (0, avoid_step * -1)
                ]

                for increment in increments:
                    break_for_loop = False
                    while True:
                        # check if the point is within the box
                        formatted_point = (
                            formatted_point[0] + increment[0],
                            formatted_point[1] + increment[1]
                        )
                        point_buffered = Point(formatted_point).buffer(radius)

                        if not box_poly.contains(point_buffered):
                            break

                        # check if the point overlaps an excellon hole
                        if self.check_point_over_excellon(pol=point_buffered, check=check_overlap).is_empty:
                            # do not add the point if it is already added
                            if formatted_point not in points:
                                points.append(formatted_point)
                                break_for_loop = True
                                break
                    if break_for_loop:
                        break
                new_x += dx
            new_y += dy

        pt_id = 0
        vor_pts_list = []
        bl_pts_list = []
        for point in points:
            pt_id += 1
            pt = Point(point)
            vor_pts_list.append(pt)
            bl_pts_list.append((point[0], point[1], 0.0))
            new_dict = {
                'point': pt,
                'geo': None,
                'height': 0.0
            }
            self.al_voronoi_geo_storage[pt_id] = deepcopy(new_dict)

        al_method = self.ui.al_method_radio.get_value()
        if al_method == 'v':
            if VORONOI_ENABLED is True:
                self.generate_voronoi_geometry(pts=vor_pts_list)
                # generate Probing GCode
                self.probing_gcode_text = self.probing_gcode(storage=self.al_voronoi_geo_storage)
            else:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Voronoi function can not be loaded.\n"
                                                            "Shapely >= 1.8 is required"))
        else:
            self.generate_bilinear_geometry(pts=bl_pts_list)
            # generate Probing GCode
            self.probing_gcode_text = self.probing_gcode(storage=self.al_bilinear_geo_storage)

        self.build_al_table_sig.emit()
        if self.ui.plot_probing_pts_cb.get_value():
            self.show_probing_geo(state=True, reset=True)
        else:
            # clear probe shapes
            self.plot_probing_geo(None, False)

    def on_add_manual_points(self):
        self.al_heights_valid = False

        xmin, ymin, xmax, ymax = self.solid_geo.bounds
        f_probe_pt = Point([xmin, xmin])
        int_keys = [int(k) for k in self.al_voronoi_geo_storage.keys()]
        new_id = max(int_keys) + 1 if int_keys else 1
        new_dict = {
            'point': f_probe_pt,
            'geo': None,
            'height': 0.0
        }
        self.al_voronoi_geo_storage[new_id] = deepcopy(new_dict)

        radius = self.ui.probe_tip_dia_entry.get_value() / 2
        fprobe_pt_buff = f_probe_pt.buffer(radius)

        self.app.inform.emit(_("Click on canvas to add a Probe Point..."))
        self.app.options['global_selection_shape'] = False

        if self.app.use_3d_engine:
            self.app.plotcanvas.graph_event_disconnect('key_press', self.app.ui.keyPressEvent)
            self.app.plotcanvas.graph_event_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
            self.app.plotcanvas.graph_event_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
        else:
            self.app.plotcanvas.graph_event_disconnect(self.app.kp)
            self.app.plotcanvas.graph_event_disconnect(self.app.mp)
            self.app.plotcanvas.graph_event_disconnect(self.app.mr)

        self.kp = self.app.plotcanvas.graph_event_connect('key_press', self.on_key_press)
        self.mr = self.app.plotcanvas.graph_event_connect('mouse_release', self.on_mouse_click_release)
        self.mm = self.app.plotcanvas.graph_event_connect('mouse_move', self.on_mouse_move)

        self.mouse_events_connected = True

        self.build_al_table_sig.emit()
        if self.ui.plot_probing_pts_cb.get_value():
            self.show_probing_geo(state=True, reset=True)
        else:
            # clear probe shapes
            self.plot_probing_geo(None, False)

        self.plot_probing_geo(geometry=fprobe_pt_buff, visibility=True, custom_color="#0000FFFA")

    def show_probing_geo(self, state, reset=False):
        self.app.log.debug("ToolLevelling.show_probing_geo() -> %s" % ('cleared' if state is False else 'displayed'))
        if reset or not state:
            self.probing_shapes.clear(update=True)
        if not state:
            return

        points_geo = []
        poly_geo = []

        al_method = self.ui.al_method_radio.get_value()
        radius = self.ui.probe_tip_dia_entry.get_value() / 2

        # voronoi diagram
        if al_method == 'v':
            # create the geometry
            for pt in self.al_voronoi_geo_storage:
                if not self.al_voronoi_geo_storage[pt]['geo']:
                    continue

                p_geo = self.al_voronoi_geo_storage[pt]['point'].buffer(radius)
                s_geo = self.al_voronoi_geo_storage[pt]['geo'].buffer(0.0000001)

                points_geo.append(p_geo)
                poly_geo.append(s_geo)

            if not points_geo and not poly_geo:
                return

            self.plot_probing_geo(geometry=points_geo, visibility=state, custom_color='#000000FF')
            self.plot_probing_geo(geometry=poly_geo, visibility=state)
        # bilinear interpolation
        elif al_method == 'b':
            bilinear_points = self.al_bilinear_geo_storage
            if not bilinear_points:
                bilinear_points = [
                    (value['point'].x, value['point'].y)
                    for value in self.al_voronoi_geo_storage.values()
                ]

            for pt in bilinear_points:

                x_pt = pt[0]
                y_pt = pt[1]
                p_geo = Point([x_pt, y_pt]).buffer(radius)

                if p_geo.is_valid:
                    points_geo.append(p_geo)

            if not points_geo:
                return

            # the area allocated to each probe point (grid cell); drawn with a
            # filled random color while no heights exist, and as an outline
            # only once the heatmap carries the height information
            heatmap = self._generate_bilinear_heatmap()
            for value in self.al_voronoi_geo_storage.values():
                cell_geo = value.get('geo')
                if cell_geo is None or cell_geo.is_empty:
                    continue
                poly_geo.append(cell_geo.buffer(0.0000001))

            if poly_geo:
                if heatmap:
                    for cell_geo in poly_geo:
                        self.add_probing_shape(
                            shape=cell_geo, color='#000000FF', face_color='#00000000', visible=True
                        )
                else:
                    self.plot_probing_geo(geometry=poly_geo, visibility=state)

            for heatmap_geo, color in heatmap:
                self.add_probing_shape(
                    shape=heatmap_geo, color=color, face_color=color, visible=True
                )

            self.plot_probing_geo(geometry=points_geo, visibility=state, custom_color='#000000FF')

    def _generate_bilinear_heatmap(self):
        if not self.al_heights_valid or not self.al_voronoi_geo_storage:
            return []

        points = [
            (value['point'].x, value['point'].y, value.get('height'))
            for value in self.al_voronoi_geo_storage.values()
        ]
        if any(
                value is None or not math.isfinite(value)
                for point in points for value in point
        ):
            return []

        grid_data = build_bilinear_grid(points, self._al_grid_tolerance())
        if grid_data is None:
            return []

        xs, ys, _grid = grid_data
        z_min = min(point[2] for point in points)
        z_max = max(point[2] for point in points)
        band_colors = (
            '#2146A659', '#167CBE59', '#00A6B259', '#31B87959',
            '#A7C63659', '#F4D44D59', '#F28E2B59', '#D7302759',
        )

        def sample_axis(values):
            interval_count = len(values) - 1
            sample_count = min(40, interval_count * 4)
            if sample_count >= interval_count:
                subdivisions = max(1, min(4, sample_count // interval_count))
                axis = [values[0]]
                for start, end in zip(values, values[1:]):
                    for subdivision in range(1, subdivisions + 1):
                        axis.append(start + (end - start) * subdivision / subdivisions)
                return axis

            span = values[-1] - values[0]
            axis = [values[0] + span * index / sample_count for index in range(sample_count + 1)]
            axis[-1] = values[-1]
            return axis

        sampled_xs = sample_axis(xs)
        sampled_ys = sample_axis(ys)
        band_tiles = {}
        for x0, x1 in zip(sampled_xs, sampled_xs[1:]):
            for y0, y1 in zip(sampled_ys, sampled_ys[1:]):
                tile = box(x0, y0, x1, y1)
                if tile.is_empty:
                    continue
                height = bilinear_offset(grid_data, (x0 + x1) / 2, (y0 + y1) / 2)
                if z_max == z_min:
                    band = len(band_colors) // 2
                else:
                    band = int((height - z_min) / (z_max - z_min) * len(band_colors))
                    band = min(max(band, 0), len(band_colors) - 1)
                band_tiles.setdefault(band, []).append(tile)

        result = []
        for band, tiles in sorted(band_tiles.items()):
            geometry = unary_union(tiles)
            if not geometry.is_empty:
                result.append((geometry, band_colors[band]))
        return result

    def plot_probing_geo(self, geometry, visibility, custom_color=None):
        if visibility:
            if self.app.use_3d_engine:
                def random_color():
                    r_color = np.random.rand(4)
                    r_color[3] = 0.5
                    return r_color
            else:
                def random_color():
                    while True:
                        r_color = np.random.rand(4)
                        r_color[3] = 0.5

                        new_color = '#'
                        for idx in range(len(r_color)):
                            new_color += '%x' % int(r_color[idx] * 255)
                        # do it until a valid color is generated
                        # a valid color has the # symbol, another 6 chars for the color and the last 2 chars for alpha
                        # for a total of 9 chars
                        if len(new_color) == 9:
                            break
                    return new_color

            try:
                # if self.app.use_3d_engine:
                #     color = "#0000FFFE"
                # else:
                #     color = "#0000FFFE"
                # for sh in points_geo:
                #     self.add_probing_shape(shape=sh, color=color, face_color=color, visible=True)

                edge_color = "#000000FF"

                try:
                    for sh in geometry:
                        if custom_color is None:
                            k = self.add_probing_shape(
                                shape=sh,
                                color=edge_color,
                                face_color=random_color(),
                                visible=True
                            )
                        else:
                            k = self.add_probing_shape(
                                shape=sh,
                                color=custom_color,
                                face_color=custom_color,
                                visible=True
                            )
                except TypeError:
                    if custom_color is None:
                        self.add_probing_shape(
                            shape=geometry, color=edge_color, face_color=random_color(), visible=True)
                    else:
                        self.add_probing_shape(
                            shape=geometry, color=custom_color, face_color=custom_color, visible=True)

                self.probing_shapes.redraw()
            except (ObjectDeleted, AttributeError) as e:
                self.app.log.error("ToolLevelling.plot_probing_geo() -> %s" % str(e))
                self.probing_shapes.clear(update=True)
            except Exception as e:
                self.app.log.error("CNCJobObject.plot_probing_geo() --> %s" % str(e))
        else:
            self.probing_shapes.clear(update=True)

    def add_probing_shape(self, **kwargs):
        key = self.probing_shapes.add(tolerance=self.drawing_tolerance, layer=0, **kwargs)
        return key

    def generate_voronoi_geometry(self, pts):
        env = self.solid_geo.envelope
        fact = 1 if self.units == 'MM' else 0.039
        env = env.buffer(fact)

        new_pts = deepcopy(pts)
        try:
            pts_union = MultiPoint(pts)
            voronoi_union = voronoi_diagram(geom=pts_union, envelope=env)
        except Exception as e:
            self.app.log.error("CNCJobObject.generate_voronoi_geometry() --> %s" % str(e))
            for pt_index in range(len(pts)):
                new_pts[pt_index] = translate(
                    new_pts[pt_index], random.random() * 1e-09, random.random() * 1e-09)

            pts_union = MultiPoint(new_pts)
            try:
                voronoi_union = voronoi_diagram(geom=pts_union, envelope=env)
            except Exception:
                return

        new_voronoi = []
        for p in voronoi_union.geoms:
            new_voronoi.append(p.intersection(env))

        for pt_key in list(self.al_voronoi_geo_storage.keys()):
            for poly in new_voronoi:
                if self.al_voronoi_geo_storage[pt_key]['point'].within(poly):
                    self.al_voronoi_geo_storage[pt_key]['geo'] = poly

    # def generate_voronoi_geometry_2(self, pts):
    #     env = self.solid_geo.envelope
    #     fact = 1 if self.units == 'MM' else 0.039
    #     env = env.buffer(fact)
    #     env_poly = voronoi_poly(tuple(env.exterior.coords))
    #
    #     new_pts = [[pt.x, pt.y] for pt in pts]
    #     print(new_pts)
    #     print(env_poly)
    #
    #     # Initialize the algorithm
    #     v = Voronoi(env_poly)
    #
    #     # calculate the Voronoi diagram
    #     try:
    #         v.create_diagram(new_pts)
    #     except AttributeError as e:
    #         self.app.log.error("CNCJobObject.generate_voronoi_geometry_2() --> %s" % str(e))
    #         new_pts_2 = []
    #         for pt_index in range(len(new_pts)):
    #             new_pts_2.append([
    #                 new_pts[pt_index][0] + random.random() * 1e-03,
    #                 new_pts[pt_index][1] + random.random() * 1e-03
    #             ])
    #
    #         try:
    #             v.create_diagram(new_pts_2)
    #         except Exception:
    #             print("Didn't work.")
    #             return
    #
    #     new_voronoi = []
    #     for p in v.sites:
    #         # p_coords = [(coord.x, coord.y) for coord in p.get_coordinates()]
    #         p_coords = [(p.x, p.y)]
    #         new_pol = Polygon(p_coords)
    #         new_voronoi.append(new_pol)
    #
    #     new_voronoi = MultiPolygon(new_voronoi)
    #
    #     # new_voronoi = []
    #     # for p in voronoi_union:
    #     #     new_voronoi.append(p.intersection(env))
    #     #
    #     for pt_key in list(self.al_voronoi_geo_storage.keys()):
    #         for poly in new_voronoi:
    #             if self.al_voronoi_geo_storage[pt_key]['point'].within(poly) or \
    #                     self.al_voronoi_geo_storage[pt_key]['point'].intersects(poly):
    #                 self.al_voronoi_geo_storage[pt_key]['geo'] = poly

    def generate_bilinear_geometry(self, pts):
        self.al_bilinear_geo_storage = pts

        # allocate an area (cell) to each probe point so it can be plotted;
        # for a regular grid the Voronoi cells are the rectangles halfway
        # between neighbouring probe points
        if VORONOI_ENABLED is True:
            self.generate_voronoi_geometry(pts=[Point((pt[0], pt[1])) for pt in pts])

    def on_mouse_move(self, event):
        """
        Callback for the mouse motion event over the plot.

        :param event: Contains information about the event.
        :return: None
        """

        if self.app.use_3d_engine:
            self.mouse_is_dragging = event.is_dragging
        else:
            self.mouse_is_dragging = self.app.plotcanvas.is_dragging

        # So it can receive key presses but not when the Tcl Shell is active
        if not self.app.ui.shell_dock.isVisible():
            if not self.app.plotcanvas.native.hasFocus():
                self.app.plotcanvas.native.setFocus()

    # To be called after clicking on the plot.
    def on_mouse_click_release(self, event):
        if self.app.use_3d_engine:
            event_pos = event.pos
            right_button = 2
        else:
            event_pos = (event.xdata, event.ydata)
            right_button = 3

        try:
            x = float(event_pos[0])
            y = float(event_pos[1])
        except TypeError:
            return
        event_pos = (x, y)

        # do paint single only for left mouse clicks
        if event.button == 1:
            check_for_exc_hole = self.ui.avoid_exc_holes_cb.get_value()

            pos = self.app.plotcanvas.translate_coords(event_pos)
            # use the snapped position as reference
            snapped_pos = self.app.geo_editor.snap(pos[0], pos[1])

            # do not add the point if is already added
            old_points_coords = [(pt['point'].x, pt['point'].y) for pt in self.al_voronoi_geo_storage.values()]
            if (snapped_pos[0], snapped_pos[1]) in old_points_coords:
                return

            # Clicked Point
            probe_pt = Point(snapped_pos)

            xxmin, yymin, xxmax, yymax = self.solid_geo.bounds
            box_geo = box(xxmin, yymin, xxmax, yymax)
            if not probe_pt.within(box_geo):
                self.app.inform.emit(_("Point is not within the object area. Choose another point."))
                return

            # check if chosen point is within an Excellon drill hole geometry
            if check_for_exc_hole is True:
                for obj_in_collection in self.app.collection.get_list():
                    if obj_in_collection.kind == 'excellon' and obj_in_collection.obj_options['plot'] is True:
                        exc_solid_geometry = MultiPolygon(obj_in_collection.solid_geometry)
                        for exc_geo in exc_solid_geometry.geoms:
                            if probe_pt.within(exc_geo):
                                self.app.inform.emit(_("Point on an Excellon drill hole. Choose another point."))
                                return

            int_keys = [int(k) for k in self.al_voronoi_geo_storage.keys()]
            new_id = max(int_keys) + 1 if int_keys else 1
            new_dict = {
                'point': probe_pt,
                'geo': None,
                'height': 0.0
            }
            self.al_voronoi_geo_storage[new_id] = deepcopy(new_dict)
            self.al_heights_valid = False

            # rebuild the al table
            self.build_al_table_sig.emit()

            radius = self.ui.probe_tip_dia_entry.get_value() / 2
            probe_pt_buff = probe_pt.buffer(radius)

            self.plot_probing_geo(geometry=probe_pt_buff, visibility=True, custom_color="#0000FFFA")

            self.app.inform.emit(_("Added a Probe Point... Click again to add another or right click to finish ..."))

        # if RMB then we exit
        elif event.button == right_button and self.mouse_is_dragging is False:
            if self.app.use_3d_engine:
                self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)
                self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_click_release)
                self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
            else:
                self.app.plotcanvas.graph_event_disconnect(self.kp)
                self.app.plotcanvas.graph_event_disconnect(self.mr)
                self.app.plotcanvas.graph_event_disconnect(self.mm)

            self.app.kp = self.app.plotcanvas.graph_event_connect('key_press', self.app.ui.keyPressEvent)
            self.app.mp = self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
            self.app.mr = self.app.plotcanvas.graph_event_connect('mouse_release',
                                                                  self.app.on_mouse_click_release_over_plot)

            # signal that the mouse events are disconnected from local methods
            self.mouse_events_connected = False

            # restore selection
            self.app.options['global_selection_shape'] = self.old_selection_state

            self.app.inform.emit(_("Finished adding Probe Points..."))

            al_method = self.ui.al_method_radio.get_value()
            if al_method == 'v':
                if VORONOI_ENABLED is True:
                    pts_list = []
                    for k in self.al_voronoi_geo_storage:
                        pts_list.append(self.al_voronoi_geo_storage[k]['point'])
                    self.generate_voronoi_geometry(pts=pts_list)

                    self.probing_gcode_text = self.probing_gcode(self.al_voronoi_geo_storage)
                else:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _("Voronoi function can not be loaded.\n"
                                                                "Shapely >= 1.8 is required"))

            # rebuild the al table
            self.build_al_table_sig.emit()

            if self.ui.plot_probing_pts_cb.get_value():
                self.show_probing_geo(state=True, reset=True)
            else:
                # clear probe shapes
                self.plot_probing_geo(None, False)

    def on_key_press(self, event):
        # events out of the self.app.collection view (it's about Project Tab) are of type int
        if isinstance(event, int):
            key = event
        # events from the GUI are of type QKeyEvent
        elif isinstance(event, QtGui.QKeyEvent):
            key = event.key()
        elif isinstance(event, mpl_key_event):  # MatPlotLib key events are trickier to interpret than the rest
            key = event.key
            key = QtGui.QKeySequence(key)

            # check for modifiers
            key_string = key.toString().lower()
            if '+' in key_string:
                mod, __, key_text = key_string.rpartition('+')
                if mod.lower() == 'ctrl':
                    # modifiers = QtCore.Qt.KeyboardModifier.ControlModifier
                    pass
                elif mod.lower() == 'alt':
                    # modifiers = QtCore.Qt.KeyboardModifier.AltModifier
                    pass
                elif mod.lower() == 'shift':
                    # modifiers = QtCore.Qt.KeyboardModifier.
                    pass
                else:
                    # modifiers = QtCore.Qt.KeyboardModifier.NoModifier
                    pass
                key = QtGui.QKeySequence(key_text)
        # events from Vispy are of type KeyEvent
        else:
            key = event.key

        # Escape = Deselect All
        if key == QtCore.Qt.Key.Key_Escape or key == 'Escape':
            if self.mouse_events_connected is True:
                self.mouse_events_connected = False
                if self.app.use_3d_engine:
                    self.app.plotcanvas.graph_event_disconnect('key_press', self.on_key_press)
                    self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_mouse_click_release)
                    self.app.plotcanvas.graph_event_disconnect('mouse_move', self.on_mouse_move)
                else:
                    self.app.plotcanvas.graph_event_disconnect(self.kp)
                    self.app.plotcanvas.graph_event_disconnect(self.mr)

                self.app.kp = self.app.plotcanvas.graph_event_connect('key_press', self.app.ui.keyPressEvent)
                self.app.mp = self.app.plotcanvas.graph_event_connect('mouse_press', self.app.on_mouse_click_over_plot)
                self.app.mr = self.app.plotcanvas.graph_event_connect('mouse_release',
                                                                      self.app.on_mouse_click_release_over_plot)
                # restore selection
                self.app.options['global_selection_shape'] = self.old_selection_state

        # Grid toggle
        if key == QtCore.Qt.Key.Key_G or key == 'G':
            self.app.ui.grid_snap_btn.trigger()

        # Jump to coords
        if key == QtCore.Qt.Key.Key_J or key == 'J':
            self.app.on_jump_to()

    def _al_grid_tolerance(self):
        """
        Tolerance used to snap probe X/Y values into a bilinear grid
        (build_bilinear_grid()), i.e. to decide which probe points share
        an X column / Y row.

        This is based on `self.app.decimals` (not `target_obj.coords_decimals`,
        which is only used later to format the levelled Z output) because
        the probe point coordinates stored in `self.al_voronoi_geo_storage`
        were themselves generated/rounded with `self.app.dec_format(...,
        self.app.decimals)` (see on_add_grid_points()); 10x the smallest
        unit at that resolution absorbs the resulting float noise without
        risking merging 2 genuinely different grid rows/columns.

        :return: tolerance (float), in the units of `self.units`.
        """
        return 10 ** -self.app.decimals * 10

    def _al_match_tolerance(self, points_xy):
        """
        Tolerance used to match a GRBL probe result back to the storage
        point it was probed for (parse_grbl_probe_result()).

        Unlike the grid tolerance above, a probe result's X/Y is echoed
        back by the controller itself and can carry real-world noise
        (step quantization, backlash, ...), so the tolerance is derived
        from the actual point layout: half of the minimum distance
        between any 2 distinct stored points (so 2 different points can
        never both match the same probe result), capped at a fixed
        absolute distance appropriate for the machine's units so that a
        sparse point layout doesn't produce an overly generous tolerance.

        :param points_xy: iterable of (x, y) tuples - the stored probe
            point coordinates.
        :return: tolerance (float), in the units of `self.units`.
        """
        cap = 0.5 if self.units == 'MM' else 0.02
        return match_tolerance(points_xy, cap)

    def autolevell_gcode_line(self, gcode_line, state):
        return level_gcode_line(gcode_line, state, self._al_offset_fn, self._al_decimals)

    def _al_prepare_interp(self, target_obj, al_method):
        """
        Validate `target_obj` / the height map and set up the per-call
        interpolation state (`self._al_points`, `self._al_grid`,
        `self._al_offset_fn`, `self._al_decimals`) used by
        autolevell_gcode_line(). Emits the app-level error/warning
        messages on failure or on grid fallback.

        Shared setup used by autolevell_gcode() to level each of a
        CNCJob object's tools' G-code, with a shared modal state across
        tools.

        :param target_obj:  the CNCJobObject to be height-compensated.
        :param al_method:   'v' (Voronoi/nearest-point) or 'b' (bilinear).
        :return:            True on success, False on error (message already emitted).
        :rtype:             bool
        """
        if target_obj is None or target_obj.is_segmented_gcode is not True:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' % _("The CNCJob object must be made from segmented G-code.")
            )
            return False

        # level_gcode_line() only understands standard G-code motion; Roland,
        # HPGL and laser preprocessors emit a completely different dialect
        # (see camlib.CNCjob.codes_split()), and solder-paste jobs are XY-only
        # dispense moves, not height-compensable milling. Refuse rather than
        # silently mis-level.
        pp_geometry_name = str(getattr(target_obj, 'pp_geometry_name', '') or '')
        pp_excellon_name = str(getattr(target_obj, 'pp_excellon_name', '') or '')
        pp_solderpaste_name = getattr(target_obj, 'pp_solderpaste_name', None)

        unsupported_markers = ('roland', 'hpgl', 'laser')
        bad_pp_name = None
        if pp_solderpaste_name is not None:
            bad_pp_name = pp_solderpaste_name
        elif any(marker in pp_geometry_name.lower() for marker in unsupported_markers):
            bad_pp_name = pp_geometry_name
        elif any(marker in pp_excellon_name.lower() for marker in unsupported_markers):
            bad_pp_name = pp_excellon_name

        if bad_pp_name is not None:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s: %s.' %
                (_("Autolevelling is not supported for this preprocessor"), bad_pp_name)
            )
            return False

        storage = self.al_voronoi_geo_storage
        if not storage or not self.al_heights_valid:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' %
                _("No height map. Probe the points or import a height map first.")
            )
            return False

        if str(target_obj.units).upper() != self.units:
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' % _("The units of the CNCJob object and of the height map do not match.")
            )
            return False

        points = [(v['point'].x, v['point'].y, v['height']) for v in storage.values()]
        self._al_points = points
        self._al_decimals = target_obj.coords_decimals

        tol = self._al_grid_tolerance()

        if al_method == 'b':
            self._al_grid = build_bilinear_grid(points, tol)
            if self._al_grid is None:
                self.app.inform.emit(
                    '[WARNING_NOTCL] %s' %
                    _("Probe points do not form a regular grid. Using nearest-point levelling.")
                )
                self._al_offset_fn = self.autolevell_voronoi
            else:
                self._al_offset_fn = self.autolevell_bilinear
        else:
            self._al_offset_fn = self.autolevell_voronoi

        return True

    def autolevell_gcode(self, target_obj, al_method):
        """
        Apply the probed height map (self.al_voronoi_geo_storage) to each
        of a CNCJob object's per-tool G-code texts
        (`target_obj.tools[k]['gcode']`). A single modal G-code state is
        shared across all tools, processed in `target_obj.tools`'s
        (dict/insertion) order - the same order export_gcode() uses to
        concatenate them into source_file - so a Z/G-mode set by 1
        tool's trailing lines carries into the next tool's leading
        lines exactly as it would in the assembled G-code.

        Not reentrant: it stores per-call interpolation state on `self`
        (`self._al_points`, `self._al_grid`, `self._al_offset_fn`,
        `self._al_decimals`). The caller must not start a 2nd call before
        the 1st one returns (e.g. from another thread).

        :param target_obj:  the CNCJobObject whose tools' G-code is to be
                             height-compensated. Not modified.
        :param al_method:   'v' (Voronoi/nearest-point) or 'b' (bilinear).
        :return:             {tooluid: levelled G-code text} for every tool
                              in target_obj.tools, or None on error.
        :rtype:              dict | None
        """
        if not self._al_prepare_interp(target_obj, al_method):
            return None

        state = new_levelling_state()
        result = {}
        for tooluid in target_obj.tools.keys():
            tool_gcode = target_obj.tools[tooluid].get('gcode') or ''
            source_lines = tool_gcode.splitlines()
            trailing_newline = tool_gcode.endswith('\n')

            new_lines = [self.autolevell_gcode_line(line, state) for line in source_lines]

            text = '\n'.join(new_lines)
            if trailing_newline:
                text += '\n'
            result[tooluid] = text

        if state['arcs'] > 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' % (_("Arc moves were not height-compensated"), state['arcs'])
            )

        if state['relative'] > 0:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s: %d' %
                (_("Relative (G91) moves were not height-compensated"), state['relative'])
            )

        return result

    def autolevell_bilinear(self, x, y):
        return bilinear_offset(self._al_grid, x, y)

    def autolevell_voronoi(self, x, y):
        return nearest_offset(self._al_points, x, y)

    def parse_grbl_probe_result(self):
        """
        Parse self.grbl_probe_result (the raw serial answer stream
        accumulated while probing) and write the resulting heights into
        self.al_voronoi_geo_storage.

        :return: True on success; False if a probe did not touch the
                 surface, or a probed point could not be matched to a
                 stored probe location, or a stored probe location did
                 not receive any result.
        :rtype:  bool
        """
        try:
            results = parse_grbl_probe_output(self.grbl_probe_result)
        except ValueError as e:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Probing failed"), str(e)))
            return False

        ox, oy, oz = self.grbl_work_offset

        points = {
            key: (value['point'].x, value['point'].y)
            for key, value in self.al_voronoi_geo_storage.items()
        }
        tol = self._al_match_tolerance(points.values())

        matched_keys = set()
        for mx, my, mz in results:
            x = mx - ox
            y = my - oy
            z = mz - oz

            key = match_nearest_point(points, x, y, tol)
            if key is None:
                self.app.inform.emit(
                    '[ERROR_NOTCL] %s' %
                    _("A probed point could not be matched to any stored probe location.")
                )
                return False

            self.al_voronoi_geo_storage[key]['height'] = z
            matched_keys.add(key)

        if matched_keys != set(self.al_voronoi_geo_storage.keys()):
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' % _("Some probe points did not receive a height value.")
            )
            return False

        self.al_heights_valid = True
        self.build_al_table_sig.emit()
        if self.ui.plot_probing_pts_cb.get_value():
            self.show_probing_geo_sig.emit(True, True)
        return True

    def on_show_al_table(self, state):
        self.ui.al_probe_points_table.show() if state else self.ui.al_probe_points_table.hide()

    def on_mode_radio(self, val):
        # reset al table
        self.ui.al_probe_points_table.setRowCount(0)

        # reset the al dict
        self.al_voronoi_geo_storage.clear()
        self.al_heights_valid = False

        # reset Voronoi Shapes
        self.probing_shapes.clear(update=True)

        # build AL table
        self.build_al_table()

        self._update_mode_radio_ui(val)

    def _update_mode_radio_ui(self, val):
        # enable/disable the widgets relevant to the selected AL mode, without
        # touching the al_voronoi_geo_storage / al_heights_valid / probing_shapes
        # state - that reset must only happen when the user changes the mode
        # radio through the UI (see on_mode_radio(), the signal-connected
        # handler); set_tool_ui() calls this helper directly so that a
        # retained (probed/imported) height map survives re-opening the tool.
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        if val == "manual":
            self.ui.al_method_radio.set_value('v')
            self.ui.al_rows_entry.setDisabled(True)
            self.ui.al_rows_label.setDisabled(True)
            self.ui.al_columns_entry.setDisabled(True)
            self.ui.al_columns_label.setDisabled(True)
            self.ui.al_method_lbl.setDisabled(True)
            self.ui.al_method_radio.setDisabled(True)
            # self.ui.avoid_exc_holes_cb.setDisabled(False)
        else:
            self.ui.al_rows_entry.setDisabled(False)
            self.ui.al_rows_label.setDisabled(False)
            self.ui.al_columns_entry.setDisabled(False)
            self.ui.al_columns_label.setDisabled(False)
            self.ui.al_method_lbl.setDisabled(False)
            self.ui.al_method_radio.setDisabled(False)
            self.ui.al_method_radio.set_value(
                self.app.options.get(
                    'tools_al_method',
                    app_defaults.get('tools_al_method', AppDefaults.factory_defaults.get('tools_al_method')))
            )
            # self.ui.avoid_exc_holes_cb.setDisabled(True)

    def on_avoid_exc_holes(self, state):
        self.ui.avoid_exc_holes_size_label.show() if state else self.ui.avoid_exc_holes_size_label.hide()
        self.ui.avoid_exc_holes_size_entry.show() if state else self.ui.avoid_exc_holes_size_entry.hide()

    def on_method_radio(self, val):
        if val == 'b':
            self.ui.al_columns_entry.setMinimum(2)
            self.ui.al_rows_entry.setMinimum(2)
        else:
            self.ui.al_columns_entry.setMinimum(1)
            self.ui.al_rows_entry.setMinimum(1)

    def on_controller_change(self):
        self.on_controller_change_alter_ui()

        # if the is empty then there is a chance that we've added probe points but the GRBL controller was selected
        # therefore no Probing GCode was generated (it is different for GRBL on how it gets it's Probing GCode
        target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        if (not self.probing_gcode_text or self.probing_gcode_text == '') and target_obj is not None:
            # generate Probing GCode
            al_method = self.ui.al_method_radio.get_value()
            storage = self.al_voronoi_geo_storage if al_method == 'v' else self.al_bilinear_geo_storage
            self.probing_gcode_text = self.probing_gcode(storage=storage)

    def on_controller_change_alter_ui(self):
        if self.ui.al_controller_combo.get_value() == 'GRBL':
            self.ui.h_gcode_button.hide()
            self.ui.view_h_gcode_button.hide()

            self.ui.import_heights_button.hide()
            self.ui.grbl_frame.show()
            self.on_grbl_search_ports(muted=True)
        else:
            self.ui.h_gcode_button.show()
            self.ui.view_h_gcode_button.show()

            self.ui.import_heights_button.show()
            self.ui.grbl_frame.hide()

    @staticmethod
    def on_grbl_list_serial_ports():
        """
        Lists serial port names.
        From here: https://stackoverflow.com/questions/12090503/listing-available-com-ports-with-python

        :raises EnvironmentError:   On unsupported or unknown platforms
        :returns:                   A list of the serial ports available on the system
        """

        if sys.platform.startswith('win'):
            ports = ['COM%s' % (i + 1) for i in range(256)]
        elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
            # this excludes your current terminal "/dev/tty"
            ports = glob.glob('/dev/tty[A-Za-z]*')
        elif sys.platform.startswith('darwin'):
            ports = glob.glob('/dev/tty.*')
        else:
            raise EnvironmentError('Unsupported platform')

        result = []
        s = serial.Serial()

        for port in ports:
            s.port = port

            try:
                s.open()
                s.close()
                result.append(port)
            except (OSError, serial.SerialException):
                # result.append(port + " (in use)")
                pass

        return result

    def on_grbl_search_ports(self, muted=None):
        port_list = self.on_grbl_list_serial_ports()
        self.ui.com_list_combo.clear()
        self.ui.com_list_combo.addItems(port_list)
        if muted is not True:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("COM list updated ..."))

    def on_grbl_connect(self):
        if self._grbl_op is not None or self._grbl_close_pending:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("GRBL is busy. Wait until the running operation stops."))
            return

        # the Connect button toggles: a click while a port is open closes it
        if self._grbl_port_ready():
            self._grbl_disconnect()
            return

        port_name = self.ui.com_list_combo.currentText()
        if " (" in port_name:
            port_name = port_name.rpartition(" (")[0]

        try:
            baudrate = int(self.ui.baudrates_list_combo.currentText())
        except ValueError:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Select a valid baudrate."))
            return

        try:
            self.grbl_ser_port = serial.serial_for_url(port_name, baudrate,
                                                       bytesize=serial.EIGHTBITS,
                                                       parity=serial.PARITY_NONE,
                                                       stopbits=serial.STOPBITS_ONE,
                                                       timeout=0.1,
                                                       xonxoff=False,
                                                       rtscts=False)

            # Toggle DTR to reset the controller loaded with GRBL (Arduino, ESP32, etc.)
            try:
                self.grbl_ser_port.dtr = False
            except IOError:
                pass

            self.grbl_ser_port.reset_input_buffer()

            try:
                self.grbl_ser_port.dtr = True
            except IOError:
                pass

            # wake up GRBL; a timer reads the answer 1 s later, so the GUI does not stop
            self.grbl_ser_port.write("\r\n\r\n".encode('utf-8'))
        except (serial.SerialException, OSError, ValueError) as err:
            self.app.log.error("ToolLevelling.on_grbl_connect() -> %s" % str(err))
            self._grbl_close_port()
            self.app.inform.emit("[ERROR_NOTCL] %s: %s" % (_("Could not connect to port"), port_name))
            return

        QtCore.QTimer.singleShot(1000, lambda: self._grbl_connect_finish(port_name))

    def _grbl_connect_finish(self, port_name):
        """
        Read the GRBL answer to the wake-up lines. Mark the port as connected
        if GRBL answered, else close the port.
        """
        port = self.grbl_ser_port
        if port is None or not getattr(port, 'is_open', False):
            return

        try:
            answer = port.readlines()
            port.reset_input_buffer()
        except (serial.SerialException, OSError) as err:
            self.app.log.error("ToolLevelling._grbl_connect_finish() -> %s" % str(err))
            answer = []

        for line in answer:
            text = line.decode('utf-8', errors='replace').lower()
            if 'ok' in text or 'grbl' in text:
                with self._grbl_op_lock:
                    # the DTR toggle reset GRBL; the machine position is still unknown
                    if self._grbl_recovery == 'needs_reset':
                        self._grbl_recovery = 'needs_home'
                self._grbl_set_ui_connected(True)
                self.app.inform.emit("%s: %s" % (_("Port connected"), port_name))
                if self._grbl_recovery is not None:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % self._grbl_recovery_text())
                return

        self._grbl_close_port()
        self.app.inform.emit("[ERROR_NOTCL] %s: %s" % (_("Could not connect to GRBL on port"), port_name))

    def _grbl_set_ui_connected(self, connected):
        """Show the connection state on the Connect button and on the tabs."""
        color = 'seagreen' if connected else 'red'
        self.ui.com_connect_button.setStyleSheet("QPushButton {background-color: %s;}" % color)
        self.ui.com_connect_button.setText(_("Connected") if connected else _("Disconnected"))
        self.ui.controller_reset_button.setDisabled(not connected)

        for idx in range(self.ui.al_toolbar.count()):
            if self.ui.al_toolbar.tabText(idx) == _("Connect"):
                self.ui.al_toolbar.tabBar.setTabTextColor(idx, QtGui.QColor(color))
            if self.ui.al_toolbar.tabText(idx) in (_("Control"), _("Sender")):
                self.ui.al_toolbar.tabBar.setTabEnabled(idx, connected)

    def _on_grbl_port_closed(self):
        """Slot of grbl_port_closed_sig (GUI thread)."""
        try:
            self._grbl_set_ui_connected(False)
        except (AttributeError, RuntimeError):
            # the UI can be already deleted when the application ends
            pass

    def _grbl_close_port(self):
        """Close the serial port now. Safe in a worker thread (the UI changes through a signal)."""
        with self._grbl_op_lock:
            port = self.grbl_ser_port
            self.grbl_ser_port = None
            self._grbl_close_pending = False
        if port is not None:
            try:
                port.close()
            except (serial.SerialException, OSError) as err:
                self.app.log.debug("ToolLevelling._grbl_close_port() -> %s" % str(err))
        self.grbl_port_closed_sig.emit()

    def _grbl_disconnect(self):
        """
        Close the port. During an operation: stop the operation (feed hold if it
        can move the machine) and close the port when the operation releases it.
        """
        with self._grbl_op_lock:
            op = self._grbl_op
            if op is not None:
                op.cancel.set()
                if op.motion:
                    self._grbl_write_realtime_locked(b'!')
                    self._grbl_recovery = 'needs_reset'
                self._grbl_close_pending = True

        if op is not None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("The port closes when the running operation stops."))
            return

        self._grbl_close_port()
        self.app.inform.emit("%s" % _("Port disconnected."))

    def _grbl_force_close(self):
        """Close the port now, also during an operation (the application ends)."""
        with self._grbl_op_lock:
            op = self._grbl_op
            if op is not None:
                op.cancel.set()
                if op.motion:
                    self._grbl_write_realtime_locked(b'!')
        self._grbl_close_port()

    def on_grbl_add_baudrate(self):
        new_bd = str(self.ui.new_baudrate_entry.get_value())
        if int(new_bd) >= 40 and self.ui.baudrates_list_combo.findText(new_bd) == -1:
            self.ui.baudrates_list_combo.addItem(new_bd)
            self.ui.baudrates_list_combo.setCurrentText(new_bd)

    def on_grbl_delete_baudrate_grbl(self):
        current_idx = self.ui.baudrates_list_combo.currentIndex()
        self.ui.baudrates_list_combo.removeItem(current_idx)

    def on_grbl_wake(self):
        # Wake up grbl
        self.grbl_ser_port.write("\r\n\r\n".encode('utf-8'))
        # Wait for GRBL controller to initialize
        time.sleep(1)

        grbl_out = deepcopy(self.grbl_ser_port.readlines())
        self.grbl_ser_port.reset_input_buffer()

        return grbl_out

    def _grbl_port_ready(self):
        """:return: True if a serial port is open."""
        port = getattr(self, 'grbl_ser_port', None)
        return port is not None and bool(getattr(port, 'is_open', False))

    def _grbl_recovery_text(self):
        if self._grbl_recovery == 'needs_reset':
            return _("GRBL needs recovery. Press Reset, then Home.")
        return _("GRBL needs recovery. Press Home.")

    def _grbl_reserve(self, name, motion=False, allow_in_recovery=False):
        """
        Reserve the serial port for 1 operation. Call it in the GUI thread,
        before the worker task is scheduled.

        :param name:              short name of the operation.
        :param motion:            True if the operation can move the machine.
        :param allow_in_recovery: True only for the home cycle, which ends the recovery.
        :return: a _GrblOperation, or None (the user was told why).
        """
        if not self._grbl_port_ready() or self._grbl_close_pending:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a GRBL controller."))
            return None

        with self._grbl_op_lock:
            if self._grbl_op is not None:
                self.app.inform.emit('[WARNING_NOTCL] %s: %s. %s' % (
                    _("GRBL is busy"), self._grbl_op.name, _("Wait, or press Stop.")))
                return None
            if motion and self._grbl_recovery is not None and not (
                    allow_in_recovery and self._grbl_recovery == 'needs_home'):
                self.app.inform.emit('[ERROR_NOTCL] %s' % self._grbl_recovery_text())
                return None
            op = _GrblOperation(name, motion)
            self._grbl_op = op
            return op

    def _grbl_release(self, op):
        """Free the serial port after the operation `op` ended; close it if a disconnect waits."""
        close_now = False
        with self._grbl_op_lock:
            if self._grbl_op is op:
                self._grbl_op = None
                close_now = self._grbl_close_pending
        if close_now:
            self._grbl_close_port()

    def _grbl_start_op(self, name, fn, motion=False, allow_in_recovery=False):
        """
        Reserve the port now (GUI thread) and run fn(op) in a worker thread.
        The port is released when fn ends, also after an exception.

        :return: True if the worker task was scheduled.
        """
        op = self._grbl_reserve(name, motion=motion, allow_in_recovery=allow_in_recovery)
        if op is None:
            return False

        def worker_task():
            try:
                fn(op)
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolLevelling._grbl_start_op(%s) -> %s" % (name, str(err)))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("The serial port failed"), str(err)))
            finally:
                self._grbl_release(op)

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})
        return True

    def _grbl_write_realtime_locked(self, data):
        """
        Write a GRBL real-time command (1 byte, no newline, no reply read).
        The caller must hold self._grbl_op_lock.

        :return: True if the byte was written.
        """
        if not self._grbl_port_ready():
            return False
        try:
            self.grbl_ser_port.write(data)
        except (serial.SerialException, OSError) as err:
            self.app.log.error("ToolLevelling._grbl_write_realtime_locked() -> %s" % str(err))
            return False
        return True

    def _grbl_transact(self, op, command, timeout=10.0, echo=True):
        """
        Send 1 line to GRBL for the operation `op`, and read the reply until
        'ok', 'error:N' or 'ALARM:N', the time limit, a stop of `op`, or a port error.

        The stop request is checked before any I/O, under the same lock that
        on_grbl_stop() uses, so no command is written after a stop. The input
        buffer is flushed before the write, so old data is not read as this reply.
        The read uses the port that was written to, never a replacement port.

        :return: (text, status). text: the decoded reply lines joined by '\\n'.
            status: 'ok', 'error', 'timeout', 'cancelled' or 'port_error'.
        """
        cmd = command.strip()
        with self._grbl_op_lock:
            if op.cancel.is_set() or self._grbl_op is not op or not self._grbl_port_ready():
                return '', 'cancelled'
            port = self.grbl_ser_port
            try:
                port.reset_input_buffer()
                port.write((cmd + '\n').encode('utf-8'))
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolLevelling._grbl_transact() -> %s" % str(err))
                return '', 'port_error'

        if echo:
            self.app.inform_shell[str, bool].emit(cmd, False)

        decoded_lines = []
        buffer = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if op.cancel.is_set():
                return '\n'.join(decoded_lines), 'cancelled'
            try:
                chunk = port.readline()
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolLevelling._grbl_transact() -> %s" % str(err))
                return '\n'.join(decoded_lines), 'port_error'
            if not chunk:
                continue

            # readline() with the port timeout can return a partial line;
            # keep the bytes until the '\n' arrives
            buffer += chunk
            while b'\n' in buffer:
                raw_line, buffer = buffer.split(b'\n', 1)
                decoded_line = raw_line.decode('utf-8', errors='replace').strip()
                if not decoded_line:
                    continue
                decoded_lines.append(decoded_line)
                if echo:
                    self.app.inform_shell.emit('\t\t\t: ' + decoded_line.upper())

                lowered = decoded_line.lower()
                if lowered == 'ok':
                    return '\n'.join(decoded_lines), 'ok'
                if lowered.startswith('error:') or lowered.startswith('alarm:'):
                    return '\n'.join(decoded_lines), 'error'

        return '\n'.join(decoded_lines), 'timeout'

    def _grbl_query_status(self, op, timeout=2.0):
        """
        Send the real-time status request '?' for the operation `op`.

        :return: the status report line, for example '<Idle|MPos:...|FS:0,0>', or None.
        """
        with self._grbl_op_lock:
            if op.cancel.is_set() or self._grbl_op is not op or not self._grbl_port_ready():
                return None
            port = self.grbl_ser_port
            try:
                port.reset_input_buffer()
                port.write(b'?')
            except (serial.SerialException, OSError) as err:
                self.app.log.error("ToolLevelling._grbl_query_status() -> %s" % str(err))
                return None

        buffer = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if op.cancel.is_set():
                return None
            try:
                chunk = port.readline()
            except (serial.SerialException, OSError):
                return None
            buffer += chunk or b''
            while b'\n' in buffer:
                raw_line, buffer = buffer.split(b'\n', 1)
                line = raw_line.decode('utf-8', errors='replace').strip()
                if line.startswith('<'):
                    return line
        return None

    def on_grbl_send_command(self):
        cmd = self.ui.grbl_command_entry.get_value()

        # show the Shell Dock
        self.app.ui.shell_dock.show()

        def worker_task():
            with self.app.proc_container.new('%s...' % _("Sending")):
                self.send_grbl_command(command=cmd)

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def send_grbl_command(self, command, echo=True):
        """

        :param command: GCode command
        :type command:  str
        :param echo:    if to send a '\n' char after
        :type echo:     bool
        :return:        the text returned by the GRBL controller after each command
        :rtype:         str
        """
        cmd = command.strip()
        if echo:
            self.app.inform_shell[str, bool].emit(cmd, False)

        # Send Gcode command to GRBL
        snd = cmd + '\n'
        self.grbl_ser_port.write(snd.encode('utf-8'))
        grbl_out = self.grbl_ser_port.readlines()
        if not grbl_out:
            self.app.inform_shell[str, bool].emit('\t\t\t: No answer\n', False)
            return ''

        decoded_lines = []
        for line in grbl_out:
            decoded_line = line.decode('utf-8', errors='replace').strip()
            decoded_lines.append(decoded_line)
            if echo:
                try:
                    self.app.inform_shell.emit('\t\t\t: ' + decoded_line.upper())
                except Exception as e:
                    self.app.log.error("CNCJobObject.send_grbl_command() --> %s" % str(e))

        return '\n'.join(decoded_lines)

    def _send_grbl_probe_command(self, command, echo=True):
        cmd = command.strip()
        if echo:
            self.app.inform_shell[str, bool].emit(cmd, False)

        self.grbl_ser_port.write((cmd + '\n').encode('utf-8'))
        decoded_lines = []
        buffer = b''
        deadline = time.monotonic() + 10.0

        while time.monotonic() < deadline:
            chunk = self.grbl_ser_port.readline()
            if not chunk:
                continue

            # readline() with the serial port timeout can hand back a partial
            # line (no trailing '\n' yet); buffer chunks and only process
            # complete lines, so a '[PRB:' split across reads is still found
            buffer += chunk
            while b'\n' in buffer:
                raw_line, buffer = buffer.split(b'\n', 1)
                decoded_line = raw_line.decode('utf-8', errors='replace').strip()
                decoded_lines.append(decoded_line)
                if echo:
                    try:
                        self.app.inform_shell.emit('\t\t\t: ' + decoded_line.upper())
                    except Exception as e:
                        self.app.log.error("CNCJobObject.send_grbl_command() --> %s" % str(e))

                if '[PRB:' in decoded_line:
                    return '\n'.join(decoded_lines)

        self.app.inform.emit(
            '[ERROR_NOTCL] %s' % _("GRBL probe timed out waiting for a probe result.")
        )
        return None

    def send_grbl_block(self, command, echo=True):
        stripped_cmd = command.strip()

        for grbl_line in stripped_cmd.split('\n'):
            if echo:
                self.app.inform_shell[str, bool].emit(grbl_line, False)

            # Send Gcode block to GRBL
            snd = grbl_line + '\n'
            self.grbl_ser_port.write(snd.encode('utf-8'))
            grbl_out = self.grbl_ser_port.readlines()

            for line in grbl_out:
                if echo:
                    try:
                        self.app.inform_shell.emit(' : ' + line.decode('utf-8').strip().upper())
                    except Exception as e:
                        self.app.log.error("CNCJobObject.send_grbl_block() --> %s" % str(e))

    def on_grbl_get_parameter(self, param):
        if '$' in param:
            param = param.replace('$', '')

        snd = '$$\n'
        self.grbl_ser_port.write(snd.encode('utf-8'))
        grbl_out = self.grbl_ser_port.readlines()
        for line in grbl_out:
            decoded_line = line.decode('utf-8')
            par = '$%s' % str(param)
            if par in decoded_line:
                result = float(decoded_line.rpartition('=')[2])
                self.app.shell_message("GRBL Parameter: %s = %s" % (str(param), str(result)), show=True)
                return result

    def on_grbl_jog(self, direction=None):
        if direction is None:
            return
        cmd = ''

        step = self.ui.jog_step_entry.get_value(),
        feedrate = self.ui.jog_fr_entry.get_value()
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
        travelz = float(
            self.app.options.get(
                "tools_al_grbl_travelz",
                app_defaults.get("tools_al_grbl_travelz", AppDefaults.factory_defaults.get("tools_al_grbl_travelz")))
        )

        if direction == 'xplus':
            cmd = "$J=G91 %s X%s F%s" % ({'IN': 'G20', 'MM': 'G21'}[self.units], str(step), str(feedrate))
        if direction == 'xminus':
            cmd = "$J=G91 %s X-%s F%s" % ({'IN': 'G20', 'MM': 'G21'}[self.units], str(step), str(feedrate))
        if direction == 'yplus':
            cmd = "$J=G91 %s Y%s F%s" % ({'IN': 'G20', 'MM': 'G21'}[self.units], str(step), str(feedrate))
        if direction == 'yminus':
            cmd = "$J=G91 %s Y-%s F%s" % ({'IN': 'G20', 'MM': 'G21'}[self.units], str(step), str(feedrate))

        if direction == 'zplus':
            cmd = "$J=G91 %s Z%s F%s" % ({'IN': 'G20', 'MM': 'G21'}[self.units], str(step), str(feedrate))
        if direction == 'zminus':
            cmd = "$J=G91 %s Z-%s F%s" % ({'IN': 'G20', 'MM': 'G21'}[self.units], str(step), str(feedrate))

        if direction == 'origin':
            cmd = "$J=G90 %s Z%s F%s" % ({'IN': 'G20', 'MM': 'G21'}[self.units], str(travelz), str(feedrate))
            self.send_grbl_command(command=cmd, echo=False)
            cmd = "$J=G90 %s X0.0 Y0.0 F%s" % ({'IN': 'G20', 'MM': 'G21'}[self.units], str(feedrate))
            self.send_grbl_command(command=cmd, echo=False)
            return

        self.send_grbl_command(command=cmd, echo=False)

    def on_grbl_zero(self, axis):
        current_mode = self.on_grbl_get_parameter('10')
        if current_mode is None:
            return

        cmd = '$10=0'
        self.send_grbl_command(command=cmd, echo=False)

        if axis == 'x':
            cmd = 'G10 L2 P1 X0'
        elif axis == 'y':
            cmd = 'G10 L2 P1 Y0'
        elif axis == 'z':
            cmd = 'G10 L2 P1 Z0'
        else:
            # all
            cmd = 'G10 L2 P1 X0 Y0 Z0'
        self.send_grbl_command(command=cmd, echo=False)

        # restore previous mode
        cmd = '$10=%d' % int(current_mode)
        self.send_grbl_command(command=cmd, echo=False)

    def on_grbl_homing(self):
        cmd = '$H'
        self.app.inform.emit("%s" % _("GRBL is doing a home cycle."))
        self.on_grbl_wake()
        self.send_grbl_command(command=cmd)

    def on_grbl_stop(self):
        """Stop the running operation at once: feed hold, and the machine needs recovery."""
        with self._grbl_op_lock:
            op = self._grbl_op
            if op is not None:
                op.cancel.set()
            written = self._grbl_write_realtime_locked(b'!')
            if written:
                self._grbl_recovery = 'needs_reset'

        if not written:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a GRBL controller."))
            return
        self.app.inform.emit('[WARNING_NOTCL] %s %s' % (
            _("Stopped. GRBL is in feed hold."), self._grbl_recovery_text()))

    def on_grbl_reset(self):
        with self._grbl_op_lock:
            op = self._grbl_op
            if op is not None:
                op.cancel.set()
            written = self._grbl_write_realtime_locked(b'\x18')
            # a reset during motion, or during recovery, loses the machine position
            if written and ((op is not None and op.motion) or self._grbl_recovery is not None):
                self._grbl_recovery = 'needs_home'

        if not written:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a GRBL controller."))
            return
        self.app.inform.emit("%s" % _("GRBL software reset was sent."))
        if self._grbl_recovery is not None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % self._grbl_recovery_text())

    def on_grbl_pause_resume(self, checked):
        if self._grbl_op is not None:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("An operation runs. Use Stop."))
            return
        if checked is False and self._grbl_recovery is not None:
            # a resume after Stop would continue the held motion
            self.app.inform.emit('[ERROR_NOTCL] %s' % self._grbl_recovery_text())
            return

        with self._grbl_op_lock:
            written = self._grbl_write_realtime_locked(b'~' if checked is False else b'!')
        if not written:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Not connected to a GRBL controller."))
            return
        self.app.inform.emit("%s" % (_("GRBL resumed.") if checked is False else _("GRBL paused.")))

    def probing_gcode(self, storage):
        """
        :param storage:         either a dict of dicts (voronoi) or a list of tuples (bilinear)
        :return:                Probing GCode
        :rtype:                 str
        """

        target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())

        p_gcode = ''
        header = ''
        time_str = "{:%A, %d %B %Y at %H:%M}".format(dt.now())

        coords = []
        al_method = self.ui.al_method_radio.get_value()
        if al_method == 'v':
            for id_key, value in storage.items():
                x = value['point'].x
                y = value['point'].y
                coords.append(
                    (
                        self.app.dec_format(x, dec=self.app.decimals),
                        self.app.dec_format(y, dec=self.app.decimals)
                    )
                )
        else:
            for pt in storage:
                x = pt[0]
                y = pt[1]
                coords.append(
                    (
                        self.app.dec_format(x, dec=self.app.decimals),
                        self.app.dec_format(y, dec=self.app.decimals)
                    )
                )

        pr_travel = self.ui.ptravelz_entry.get_value()
        probe_fr = self.ui.feedrate_probe_entry.get_value()
        pr_depth = self.ui.pdepth_entry.get_value()
        controller = self.ui.al_controller_combo.get_value()

        header += '(G-CODE GENERATED BY FLATCAM v%s - www.flatcam.org - Version Date: %s)\n' % \
                  (str(self.app.version), str(self.app.version_date)) + '\n'

        header += '(This is a autolevelling probing GCode.)\n' \
                  '(Make sure that before you start the job you first do a zero for all axis.)\n\n'

        header += '(Name: ' + str(target_obj.obj_options['name']) + ')\n'
        header += '(Type: ' + "Autolevelling Probing GCode " + ')\n'

        header += '(Units: ' + self.units.upper() + ')\n'
        header += '(Created on ' + time_str + ')\n'

        # commands
        if controller == 'MACH3':
            probing_command = 'G31'
            # probing_var = '#2002'
            openfile_command = 'M40'
            closefile_command = 'M41'
        elif controller == 'MACH4':
            probing_command = 'G31'
            # probing_var = '#5063'
            openfile_command = 'M40'
            closefile_command = 'M41'
        elif controller == 'LinuxCNC':
            probing_command = 'G38.2'
            # probing_var = '#5422'
            openfile_command = '(PROBEOPEN a_probing_points_file.txt)'
            closefile_command = '(PROBECLOSE)'
        elif controller == 'GRBL':
            # do nothing here because the Probing GCode for GRBL is obtained differently
            return
        else:
            self.app.log.debug("CNCJobObject.probing_gcode() -> controller not supported")
            return

        # #############################################################################################################
        # ########################### GCODE construction ##############################################################
        # #############################################################################################################

        # header
        p_gcode += header + '\n'
        # supplementary message for LinuxCNC
        if controller == 'LinuxCNC':
            p_gcode += "The file with the stored probing points can be found\n" \
                       "in the configuration folder for LinuxCNC.\n" \
                       "The name of the file is: a_probing_points_file.txt.\n"
        # units
        p_gcode += 'G21\n' if self.units == 'MM' else 'G20\n'
        # reference mode = absolute
        p_gcode += 'G90\n'
        # open a new file
        p_gcode += openfile_command + '\n'
        # move to safe height (probe travel Z)
        p_gcode += 'G0 Z%s\n' % str(self.app.dec_format(pr_travel, target_obj.coords_decimals))

        # probing points
        for idx, xy_tuple in enumerate(coords, 1):  # index starts from 1
            x = xy_tuple[0]
            y = xy_tuple[1]
            # move to probing point
            p_gcode += "G0 X%sY%s\n" % (
                str(self.app.dec_format(x, target_obj.coords_decimals)),
                str(self.app.dec_format(y, target_obj.coords_decimals))
            )
            # do the probing
            p_gcode += "%s Z%s F%s\n" % (
                probing_command,
                str(self.app.dec_format(pr_depth, target_obj.coords_decimals)),
                str(self.app.dec_format(probe_fr, target_obj.fr_decimals)),
            )
            # store in a global numeric variable the value of the detected probe Z
            # I offset the global numeric variable by 500 so, it does not conflict with something else
            # temp_var = int(idx + 500)
            # p_gcode += "#%d = %s\n" % (temp_var, probing_var)

            # move to safe height (probe travel Z)
            p_gcode += 'G0 Z%s\n' % str(self.app.dec_format(pr_travel, target_obj.coords_decimals))

        # close the file
        p_gcode += closefile_command + '\n'
        # finish the GCode
        p_gcode += 'M2'

        return p_gcode

    def on_save_probing_gcode(self):
        lines = StringIO(self.probing_gcode_text)
        app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults

        _filter_ = self.app.options.get(
            'cncjob_save_filters',
            app_defaults.get('cncjob_save_filters', AppDefaults.factory_defaults.get('cncjob_save_filters'))
        )
        name = "probing_gcode"
        try:
            dir_file_to_save = self.app.get_last_save_folder() + '/' + str(name)
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                directory=dir_file_to_save,
                ext_filter=_filter_
            )
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                ext_filter=_filter_)

        if filename == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Export cancelled ..."))
            return
        else:
            try:
                force_windows_line_endings = self.app.options.get(
                    'cncjob_line_ending',
                    app_defaults.get('cncjob_line_ending', AppDefaults.factory_defaults.get('cncjob_line_ending'))
                )
                if force_windows_line_endings and sys.platform != 'win32':
                    with open(filename, 'w', newline='\r\n') as f:
                        for line in lines:
                            f.write(line)
                else:
                    with open(filename, 'w') as f:
                        for line in lines:
                            f.write(line)
            except FileNotFoundError:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No such file or directory"))
                return
            except PermissionError:
                self.app.inform.emit(
                    '[WARNING] %s' % _("Permission denied, saving not possible.\n"
                                       "Most likely another app is holding the file open and not accessible.")
                )
                return 'fail'

    def on_edit_probing_gcode(self):
        self.app.proc_container.view.set_busy('%s...' % _("Loading"))

        gco = self.probing_gcode_text
        if gco is None or gco == '':
            self.app.inform.emit('[WARNING_NOTCL] %s...' % _('There is nothing to view'))
            return

        self.gcode_viewer_tab = AppTextEditor(app=self.app, plain_text=True)

        # add the tab if it was closed
        self.app.ui.plot_tab_area.addTab(self.gcode_viewer_tab, '%s' % _("Code Viewer"))
        self.gcode_viewer_tab.setObjectName('code_viewer_tab')

        # delete the absolute and relative position and messages in the infobar
        self.app.ui.position_label.setText("")
        self.app.ui.rel_position_label.setText("")

        self.gcode_viewer_tab.code_editor.completer_enable = False
        self.gcode_viewer_tab.buttonRun.hide()

        # Switch plot_area to CNCJob tab
        self.app.ui.plot_tab_area.setCurrentWidget(self.gcode_viewer_tab)

        self.gcode_viewer_tab.t_frame.hide()
        # then append the text from GCode to the text editor
        try:
            self.gcode_viewer_tab.load_text(gco, move_to_start=True, clear_text=True)
        except Exception as e:
            self.app.log.error('FlatCAMCNCJob.on_edit_probing_gcode() -->%s' % str(e))
            return

        self.gcode_viewer_tab.t_frame.show()
        self.app.proc_container.view.set_idle()

        self.gcode_viewer_tab.buttonSave.hide()
        self.gcode_viewer_tab.buttonOpen.hide()
        self.gcode_viewer_tab.buttonPrint.hide()
        self.gcode_viewer_tab.buttonPreview.hide()
        self.gcode_viewer_tab.buttonReplace.hide()
        self.gcode_viewer_tab.sel_all_cb.hide()
        self.gcode_viewer_tab.entryReplace.hide()

        self.gcode_viewer_tab.button_update_code.show()

        # self.gcode_viewer_tab.code_editor.setReadOnly(True)

        self.gcode_viewer_tab.button_update_code.clicked.connect(self.on_update_probing_gcode)

        self.app.inform.emit('[success] %s...' % _('Loaded Machine Code into Code Viewer'))

    def on_update_probing_gcode(self):
        self.probing_gcode_text = self.gcode_viewer_tab.code_editor.toPlainText()

    def on_import_height_map(self):
        """
        Import the height map file into the app
        :return:
        :rtype:
        """

        _filter_ = "Text File .txt (*.txt);;All Files (*.*)"
        try:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Import Height Map"),
                                                                 directory=self.app.get_last_folder(),
                                                                 filter=_filter_)
        except TypeError:
            filename, _f = QtWidgets.QFileDialog.getOpenFileName(caption=_("Import Height Map"),
                                                                 filter=_filter_)

        filename = str(filename)

        if filename == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))
        else:
            self.app.worker_task.emit({'fcn': self.import_height_map, 'params': [filename]})

    def import_height_map(self, filename):
        """

        :param filename:
        :type filename:
        :return:
        :rtype:
        """
        # self.units is only ever (re)set in set_ui(); refresh it here (same
        # reasoning as apply_autolevel()) so _al_match_tolerance()'s unit-aware
        # cap and the later units check in _al_prepare_interp() use the app's
        # current units, not a stale value.
        self.units = self.app.app_units.upper()

        try:
            if filename:
                with open(filename, 'r') as f:
                    stream = f.readlines()
            else:
                return
        except IOError:
            self.app.log.error("Failed to open height map file: %s" % filename)
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to open height map file"), filename))
            return

        rows = []
        for line_no, line in enumerate(stream, start=1):
            parsed = parse_height_map_line(line)
            if parsed is None:
                if line.strip() != '':
                    self.app.log.debug(
                        "ToolLevelling.import_height_map() -> could not parse line %d: %r" % (line_no, line)
                    )
                continue
            rows.append(parsed)

        if not rows:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The height map file has no valid rows."))
            self.al_heights_valid = False
            return

        storage = self.al_voronoi_geo_storage
        if storage:
            points_xy = {key: (v['point'].x, v['point'].y) for key, v in storage.items()}
            tol = self._al_match_tolerance(list(points_xy.values()))

            matched_keys = set()
            unmatched_rows = 0
            for x, y, z in rows:
                key = match_nearest_point(points_xy, x, y, tol)
                if key is None:
                    unmatched_rows += 1
                    continue
                storage[key]['height'] = z
                matched_keys.add(key)

            if unmatched_rows:
                self.app.inform.emit(
                    '[WARNING_NOTCL] %s: %d' %
                    (_("Height map rows could not be matched to a probe point"), unmatched_rows)
                )

            missing = set(storage.keys()) - matched_keys
            if missing:
                self.app.inform.emit(
                    '[ERROR_NOTCL] %s' %
                    _("Some probe points have no matching row in the height map file.")
                )
                self.al_heights_valid = False
                return
        else:
            for idx, (x, y, z) in enumerate(rows, start=1):
                storage[idx] = {'point': Point((x, y)), 'geo': None, 'height': z}

        self.al_heights_valid = True
        self.build_al_table_sig.emit()
        if self.ui.plot_probing_pts_cb.get_value():
            self.show_probing_geo_sig.emit(True, True)

    def on_grbl_autolevel(self):
        # show the Shell Dock
        self.app.ui.shell_dock.show()

        def worker_task():
            with self.app.proc_container.new('%s...' % _("Sending")):
                self.grbl_probe_result = ''
                pr_travelz = str(self.ui.ptravelz_entry.get_value())
                probe_fr = str(self.ui.feedrate_probe_entry.get_value())
                pr_depth = str(self.ui.pdepth_entry.get_value())

                cmd = 'G21\n'
                self.send_grbl_command(command=cmd)
                cmd = 'G90\n'
                self.send_grbl_command(command=cmd)

                try:
                    wcs_output = self.send_grbl_command(command='$G\n')
                    active_wcs = parse_grbl_active_wcs(wcs_output)
                    offset_output = self.send_grbl_command(command='$#\n')
                    self.grbl_work_offset = parse_grbl_work_offset(offset_output, active_wcs)
                except ValueError as e:
                    self.app.inform.emit(
                        '[ERROR_NOTCL] %s: %s' % (_("Could not read the GRBL work offset"), str(e))
                    )
                    return

                for pt_key in self.al_voronoi_geo_storage:
                    x = str(self.al_voronoi_geo_storage[pt_key]['point'].x)
                    y = str(self.al_voronoi_geo_storage[pt_key]['point'].y)

                    cmd = 'G0 Z%s\n' % pr_travelz
                    self.send_grbl_command(command=cmd)
                    cmd = 'G0 X%s Y%s\n' % (x, y)
                    self.send_grbl_command(command=cmd)
                    cmd = 'G38.2 Z%s F%s' % (pr_depth, probe_fr)
                    output = self._send_grbl_probe_command(command=cmd)
                    if output is None:
                        return

                    self.grbl_probe_result += output + '\n'

                cmd = 'M2\n'
                self.send_grbl_command(command=cmd)
                self.app.inform.emit('%s' % _("Finished probing. Doing the autolevelling."))

                if self.parse_grbl_probe_result():
                    self.apply_autolevel_sig.emit()

        self.app.inform.emit('%s' % _("Sending probing GCode to the GRBL controller."))
        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def on_grbl_heightmap_save(self):
        if not self.al_heights_valid or not self._height_map_is_complete():
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Empty GRBL heightmap."))
            return

        _filter_ = "Text File .txt (*.txt);;All Files (*.*)"
        name = "probing_gcode"
        try:
            dir_file_to_save = self.app.get_last_save_folder() + '/' + str(name)
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                directory=dir_file_to_save,
                ext_filter=_filter_
            )
        except TypeError:
            filename, _f = FCFileSaveDialog.get_saved_filename(
                caption=_("Export Code ..."),
                ext_filter=_filter_)

        if filename == '':
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Export cancelled ..."))
            return
        else:
            try:
                app_defaults = getattr(self.app, "defaults", None) or AppDefaults.factory_defaults
                force_windows_line_endings = self.app.options.get(
                    'cncjob_line_ending',
                    app_defaults.get('cncjob_line_ending', AppDefaults.factory_defaults.get('cncjob_line_ending'))
                )
                open_kwargs = {'newline': '\r\n'} if force_windows_line_endings and sys.platform != 'win32' else {}
                with open(filename, 'w', **open_kwargs) as f:
                    for value in self.al_voronoi_geo_storage.values():
                        f.write('%s,%s,%s\n' % (value['point'].x, value['point'].y, value['height']))
            except FileNotFoundError:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("No such file or directory"))
                return
            except PermissionError:
                self.app.inform.emit(
                    '[WARNING] %s' % _("Permission denied, saving not possible.\n"
                                       "Most likely another app is holding the file open and not accessible.")
                )
                return 'fail'

    def apply_autolevel(self):
        """
        Apply the probed/imported height map to the currently selected
        CNCJob object's G-code and create a new '<name>_levelled' CNCJob
        object holding the result. The source object is left unchanged.

        Runs on the GUI thread (connected to apply_autolevel_sig, emitted
        from worker threads after a successful probe or height-map
        import), reads the widgets it needs here, then hands the actual
        levelling + object creation off to a worker thread.
        """
        if self._al_apply_running:
            self.app.inform.emit(
                '[WARNING_NOTCL] %s' % _("Autolevelling apply is already running.")
            )
            return

        # self.units is only ever (re)set in set_ui(); refresh it here so the
        # units check in _al_prepare_interp() compares against the app's
        # *current* units rather than whatever was active when this tool was
        # last opened. This matters because AppObject.new_object() converts
        # the new object's units to the app's units right after obj_init()
        # runs (and CNCJobObject.convert_units() does not scale G-code), so a
        # stale self.units here could silently pass a mismatched height map.
        self.units = self.app.app_units.upper()

        target_obj = self.app.collection.get_by_name(self.ui.object_combo.get_value())
        al_method = self.ui.al_method_radio.get_value()

        if target_obj is None or target_obj.kind != 'cncjob':
            self.app.inform.emit(
                '[ERROR_NOTCL] %s' % _("No CNCJob object selected for autolevelling.")
            )
            return

        self._al_apply_running = True

        def worker_task():
            try:
                with self.app.proc_container.new('%s...' % _("Autolevelling")):
                    tools_gcode = self.autolevell_gcode(target_obj, al_method)
                    if tools_gcode is None:
                        return

                    outname = target_obj.obj_options['name'] + '_levelled'

                    def obj_init(new_obj, app_obj):
                        # copy over the tools/options needed to reconstruct the source_file,
                        # gcode_parsed (plot) and solid_geometry from the levelled per-tool
                        # G-code, exactly as export_gcode()/plot() rebuild them for any other
                        # CNCJob object.
                        #
                        # CNCJobObject.ser_attrs (appObjects/CNCJobObject.py) is the list used
                        # to save/load a project, but it does not cover everything gcode_parse()
                        # / codes_split() need (pp_geometry_name, pp_excellon_name,
                        # pp_solderpaste_name, exc_tools aren't in it - project load rebuilds
                        # those separately), so it isn't a drop-in replacement here; the explicit
                        # list below is extended instead of switching to ser_attrs.
                        new_obj.obj_options.update(
                            {k: v for k, v in target_obj.obj_options.items() if k != 'name'}
                        )
                        new_obj.units = target_obj.units
                        new_obj.multitool = target_obj.multitool
                        new_obj.used_tools = list(target_obj.used_tools)
                        new_obj.gc_start = target_obj.gc_start
                        new_obj.prepend_snippet = target_obj.prepend_snippet
                        new_obj.append_snippet = target_obj.append_snippet
                        new_obj.is_segmented_gcode = target_obj.is_segmented_gcode
                        new_obj.pp_geometry_name = getattr(target_obj, 'pp_geometry_name', 'default')
                        new_obj.pp_excellon_name = getattr(target_obj, 'pp_excellon_name', 'default')
                        new_obj.pp_solderpaste_name = getattr(target_obj, 'pp_solderpaste_name', None)
                        new_obj.exc_tools = deepcopy(getattr(target_obj, 'exc_tools', {}))
                        # deepcopy(target_obj.tools) also carries over each tool's
                        # 'solid_geometry' unchanged, on purpose: levelling only ever
                        # rewrites the Z word of cutting moves, never X/Y, so the XY
                        # footprint stays identical to the source object's.
                        #
                        # 'multigeo' is intentionally NOT copied here: it is a
                        # GeometryObject-only flag (single vs. multi-tool *Geometry*
                        # source). CNCJob objects track multi-tool-ness with
                        # 'multitool' instead, which is copied above.
                        new_obj.tools = deepcopy(target_obj.tools)

                        # accumulate the object-level gcode / gcode_parsed across all tools,
                        # in target_obj.tools order (the same order export_gcode() concatenates
                        # them into source_file); gcode_parse() only ever sets self.gcode_parsed
                        # from self.gcode, so calling it once per tool and keeping only the last
                        # result would silently drop every other tool's geometry
                        total_gcode = ''
                        total_gcode_parsed = []
                        for tooluid, levelled_text in tools_gcode.items():
                            if tooluid not in new_obj.tools:
                                continue
                            # gcode_parse() parses self.gcode (not the per-tool dict), so it
                            # has to be set as the "current" gcode for the duration of the call
                            new_obj.gcode = levelled_text
                            tool_gcode_parsed = new_obj.gcode_parse(
                                tool_data=new_obj.tools[tooluid].get('data', {})
                            )
                            new_obj.tools[tooluid]['gcode_parsed'] = tool_gcode_parsed
                            new_obj.tools[tooluid]['gcode'] = levelled_text

                            total_gcode += levelled_text
                            total_gcode_parsed += tool_gcode_parsed

                        new_obj.gcode = total_gcode
                        new_obj.gcode_parsed = total_gcode_parsed
                        new_obj.create_geometry()

                    ret = self.app.app_obj.new_object('cncjob', outname, obj_init)
                    if ret == 'fail':
                        self.app.inform.emit(
                            '[ERROR_NOTCL] %s' % _("Failed to create the levelled CNCJob object.")
                        )
                        return

                    self.app.inform.emit('[success] %s' % _("Finished autolevelling."))
            finally:
                self._al_apply_running = False

        self.app.worker_task.emit({'fcn': worker_task, 'params': []})

    def ui_connect(self):
        self.ui.al_add_button.clicked.connect(self.on_add_al_probepoints)
        self.ui.show_al_table.stateChanged.connect(self.on_show_al_table)

    def ui_disconnect(self):
        try:
            self.ui.al_add_button.clicked.disconnect()
        except (TypeError, AttributeError):
            pass

        try:
            self.ui.show_al_table.stateChanged.disconnect()
        except (TypeError, AttributeError):
            pass

    def reset_fields(self):
        self.al_heights_valid = False
        self.ui.object_combo.setRootModelIndex(self.app.collection.index(0, 0, QtCore.QModelIndex()))


class LevelUI:
    pluginName = _("Levelling")

    def __init__(self, layout, app):
        self.app = app
        self.decimals = self.app.decimals
        self.layout = layout

        self.tools_frame = QtWidgets.QFrame()
        self.tools_frame.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.tools_frame)
        self.tools_box = QtWidgets.QVBoxLayout()
        self.tools_box.setContentsMargins(0, 0, 0, 0)
        self.tools_frame.setLayout(self.tools_box)

        self.title_box = QtWidgets.QHBoxLayout()
        self.tools_box.addLayout(self.title_box)

        # ## Title
        title_label = FCLabel("%s" % self.pluginName, size=16, bold=True)
        title_label.setToolTip(
            _("Generate CNC Code with auto-levelled paths.")
        )

        self.title_box.addWidget(title_label)

        # App Level label
        self.level = QtWidgets.QToolButton()
        self.level.setToolTip(
            _(
                "Beginner Mode - many parameters are hidden.\n"
                "Advanced Mode - full control.\n"
                "Permanent change is done in 'Preferences' menu."
            )
        )
        # self.level.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.level.setCheckable(True)
        self.title_box.addWidget(self.level)

        self.obj_combo_label = FCLabel('%s' % _("Source Object"), color='darkorange', bold=True)
        self.obj_combo_label.setToolTip(
            _("CNCJob source object to be levelled.")
        )

        self.tools_box.addWidget(self.obj_combo_label)

        # #############################################################################################################
        # ################################ The object to be Auto-levelled ##############################################
        # #############################################################################################################
        self.object_combo = FCComboBox()
        self.object_combo.setModel(self.app.collection)
        self.object_combo.setRootModelIndex(self.app.collection.index(3, 0, QtCore.QModelIndex()))
        # self.object_combo.setCurrentIndex(1)
        self.object_combo.is_last = True

        self.tools_box.addWidget(self.object_combo)

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # self.tools_box.addWidget(separator_line)

        # Autolevelling
        self.al_frame = QtWidgets.QFrame()
        self.al_frame.setContentsMargins(0, 0, 0, 0)
        self.tools_box.addWidget(self.al_frame)

        self.al_box = QtWidgets.QVBoxLayout()
        self.al_box.setContentsMargins(0, 0, 0, 0)
        self.al_frame.setLayout(self.al_box)
        self.al_frame.setDisabled(True)

        grid0 = GLay(v_spacing=5, h_spacing=3)
        self.al_box.addLayout(grid0)

        self.al_title = FCLabel('%s' % _("Probe Points Table"), bold=True)
        self.al_title.setToolTip(_("Generate GCode that will obtain the height map"))

        self.show_al_table = FCCheckBox(_("Show"))
        self.show_al_table.setToolTip(_("Toggle the display of the Probe Points table."))
        self.show_al_table.setChecked(True)

        hor_lay = QtWidgets.QHBoxLayout()
        hor_lay.addWidget(self.al_title)
        hor_lay.addStretch()
        hor_lay.addWidget(self.show_al_table, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        grid0.addLayout(hor_lay, 0, 0, 1, 2)

        # #############################################################################################################
        # Tool Table Frame
        # #############################################################################################################
        tt_frame = FCFrame()
        self.al_box.addWidget(tt_frame)

        # Grid Layout
        tool_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 1])
        tt_frame.setLayout(tool_grid)

        # Probe Points table
        self.al_probe_points_table = FCTable()
        self.al_probe_points_table.setColumnCount(3)
        self.al_probe_points_table.setColumnWidth(0, 20)
        self.al_probe_points_table.setHorizontalHeaderLabels(['#', _('X-Y Coordinates'), _('Height')])

        tool_grid.addWidget(self.al_probe_points_table, 0, 0, 1, 2)

        # Plot Probe Points
        self.plot_probing_pts_cb = FCCheckBox(_("Plot probing points"))
        self.plot_probing_pts_cb.setToolTip(
            _("Plot the probing points in the table.\n"
              "If a Voronoi method is used then\n"
              "the Voronoi areas are also plotted.")
        )
        tool_grid.addWidget(self.plot_probing_pts_cb, 2, 0, 1, 2)

        # Avoid Excellon holes
        self.avoid_exc_holes_cb = FCCheckBox(_("Avoid Excellon holes"))
        self.avoid_exc_holes_cb.setToolTip(
            _("When active, the user cannot add probe points over a drill hole.")
        )
        tool_grid.addWidget(self.avoid_exc_holes_cb, 4, 0, 1, 2)

        # Avoid Excellon holes Size
        self.avoid_exc_holes_size_label = FCLabel('%s:' % _("Avoid Step"))
        self.avoid_exc_holes_size_label.setToolTip(
            _("The incremental size to move to the side, to avoid an Excellon hole.")
        )

        self.avoid_exc_holes_size_entry = FCDoubleSpinner()
        self.avoid_exc_holes_size_entry.set_precision(self.decimals)
        self.avoid_exc_holes_size_entry.set_range(0.0000, 99999.0000)

        tool_grid.addWidget(self.avoid_exc_holes_size_label, 6, 0)
        tool_grid.addWidget(self.avoid_exc_holes_size_entry, 6, 1, 1, 1)

        # #############################################################################################################
        # ############### Probe GCode Generation ######################################################################
        # #############################################################################################################
        self.probe_gc_label = FCLabel('%s' % _("Parameters"), color='blue', bold=True)
        self.probe_gc_label.setToolTip(
            _("Will create a GCode which will be sent to the controller,\n"
              "either through a file or directly, with the intent to get the height map\n"
              "that is to modify the original GCode to level the cutting height.")
        )
        self.al_box.addWidget(self.probe_gc_label)

        tp_frame = FCFrame()
        self.al_box.addWidget(tp_frame)

        # Grid Layout
        param_grid = GLay(v_spacing=5, h_spacing=3)
        tp_frame.setLayout(param_grid)

        # Probe Diameter
        self.probe_tip_dia_label = FCLabel('%s:' % _("Probe Tip Dia"))
        self.probe_tip_dia_label.setToolTip(
            _("The probe tip diameter.")
        )
        self.probe_tip_dia_entry = FCDoubleSpinner()
        self.probe_tip_dia_entry.set_precision(self.decimals)
        self.probe_tip_dia_entry.set_range(0.0000, 10.0000)

        param_grid.addWidget(self.probe_tip_dia_label, 0, 0)
        param_grid.addWidget(self.probe_tip_dia_entry, 0, 1)

        # Travel Z Probe
        self.ptravelz_label = FCLabel('%s:' % _("Probe Z travel"))
        self.ptravelz_label.setToolTip(
            _("The safe Z for probe travelling between probe points.")
        )
        self.ptravelz_entry = FCDoubleSpinner()
        self.ptravelz_entry.set_precision(self.decimals)
        self.ptravelz_entry.set_range(0.0000, 10000.0000)

        param_grid.addWidget(self.ptravelz_label, 2, 0)
        param_grid.addWidget(self.ptravelz_entry, 2, 1)

        # Probe depth
        self.pdepth_label = FCLabel('%s:' % _("Probe Z depth"))
        self.pdepth_label.setToolTip(
            _("The maximum depth that the probe is allowed\n"
              "to probe. Negative value, in current units.")
        )
        self.pdepth_entry = FCDoubleSpinner()
        self.pdepth_entry.set_precision(self.decimals)
        self.pdepth_entry.set_range(-910000.0000, 0.0000)

        param_grid.addWidget(self.pdepth_label, 4, 0)
        param_grid.addWidget(self.pdepth_entry, 4, 1)

        # Probe feedrate
        self.feedrate_probe_label = FCLabel('%s:' % _("Probe Feedrate"))
        self.feedrate_probe_label.setToolTip(
           _("The feedrate used while the probe is probing.")
        )
        self.feedrate_probe_entry = FCDoubleSpinner()
        self.feedrate_probe_entry.set_precision(self.decimals)
        self.feedrate_probe_entry.set_range(0, 910000.0000)

        param_grid.addWidget(self.feedrate_probe_label, 6, 0)
        param_grid.addWidget(self.feedrate_probe_entry, 6, 1)

        separator_line = QtWidgets.QFrame()
        separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        param_grid.addWidget(separator_line, 8, 0, 1, 2)

        # AUTOLEVELL MODE
        al_mode_lbl = FCLabel('%s' % _("Mode"), bold=True)
        al_mode_lbl.setToolTip(_("Choose a mode for height map generation.\n"
                                 "- Manual: will pick a selection of probe points by clicking on canvas\n"
                                 "- Grid: will automatically generate a grid of probe points"))

        self.al_mode_radio = RadioSet(
            [
                {'label': _('Manual'), 'value': 'manual'},
                {'label': _('Grid'), 'value': 'grid'}
            ])
        param_grid.addWidget(al_mode_lbl, 10, 0)
        param_grid.addWidget(self.al_mode_radio, 10, 1)

        # AUTOLEVELL METHOD
        self.al_method_lbl = FCLabel('%s:' % _("Method"))
        self.al_method_lbl.setToolTip(_("Choose a method for approximation of heights from autolevelling data.\n"
                                        "- Voronoi: will generate a Voronoi diagram\n"
                                        "- Bilinear: will use bilinear interpolation. Usable only for grid mode."))

        self.al_method_radio = RadioSet(
            [
                {'label': _('Voronoi'), 'value': 'v'},
                {'label': _('Bilinear'), 'value': 'b'}
            ])
        self.al_method_lbl.setDisabled(True)
        self.al_method_radio.setDisabled(True)
        self.al_method_radio.set_value('v')

        param_grid.addWidget(self.al_method_lbl, 12, 0)
        param_grid.addWidget(self.al_method_radio, 12, 1)

        # ## Columns
        self.al_columns_entry = FCSpinner()
        self.al_columns_entry.setMinimum(2)

        self.al_columns_label = FCLabel('%s:' % _("Columns"))
        self.al_columns_label.setToolTip(
            _("The number of grid columns.")
        )
        param_grid.addWidget(self.al_columns_label, 14, 0)
        param_grid.addWidget(self.al_columns_entry, 14, 1)

        # ## Rows
        self.al_rows_entry = FCSpinner()
        self.al_rows_entry.setMinimum(2)

        self.al_rows_label = FCLabel('%s:' % _("Rows"))
        self.al_rows_label.setToolTip(
            _("The number of grid rows.")
        )
        param_grid.addWidget(self.al_rows_label, 16, 0)
        param_grid.addWidget(self.al_rows_entry, 16, 1)

        self.al_add_button = FCButton(_("Add Probe Points"))
        self.al_box.addWidget(self.al_add_button)

        # #############################################################################################################
        # Controller Frame
        # #############################################################################################################
        self.al_controller_label = FCLabel('%s' % _("Controller"), color='red', bold=True)
        self.al_controller_label.setToolTip(
            _("The kind of controller for which to generate\n"
              "height map gcode.")
        )
        self.al_box.addWidget(self.al_controller_label)

        self.c_frame = FCFrame()
        self.al_box.addWidget(self.c_frame)

        ctrl_grid = GLay(v_spacing=5, h_spacing=3)
        self.c_frame.setLayout(ctrl_grid)

        self.al_controller_combo = FCComboBox()
        self.al_controller_combo.addItems(["MACH3", "MACH4", "LinuxCNC", "GRBL"])
        ctrl_grid.addWidget(self.al_controller_combo, 0, 0, 1, 2)

        # #############################################################################################################
        # ########################## GRBL frame #######################################################################
        # #############################################################################################################
        self.grbl_frame = QtWidgets.QFrame()
        self.grbl_frame.setContentsMargins(0, 0, 0, 0)
        ctrl_grid.addWidget(self.grbl_frame, 2, 0, 1, 2)

        self.grbl_box = QtWidgets.QVBoxLayout()
        self.grbl_box.setContentsMargins(0, 0, 0, 0)
        self.grbl_frame.setLayout(self.grbl_box)

        # #############################################################################################################
        # ########################## GRBL TOOLBAR #####################################################################
        # #############################################################################################################
        self.al_toolbar = FCDetachableTab(protect=True)
        self.al_toolbar.setTabsClosable(False)
        self.al_toolbar.useOldIndex(True)
        self.al_toolbar.set_detachable(val=False)
        self.grbl_box.addWidget(self.al_toolbar)

        # GRBL Connect TAB
        self.gr_conn_tab = QtWidgets.QWidget()
        self.gr_conn_tab.setObjectName("connect_tab")
        self.gr_conn_tab_layout = QtWidgets.QVBoxLayout(self.gr_conn_tab)
        self.gr_conn_tab_layout.setContentsMargins(2, 2, 2, 2)
        # self.gr_conn_scroll_area = VerticalScrollArea()
        # self.gr_conn_tab_layout.addWidget(self.gr_conn_scroll_area)
        self.al_toolbar.addTab(self.gr_conn_tab, _("Connect"))

        # GRBL Control TAB
        self.gr_ctrl_tab = QtWidgets.QWidget()
        self.gr_ctrl_tab.setObjectName("connect_tab")
        self.gr_ctrl_tab_layout = QtWidgets.QVBoxLayout(self.gr_ctrl_tab)
        self.gr_ctrl_tab_layout.setContentsMargins(2, 2, 2, 2)

        # self.gr_ctrl_scroll_area = VerticalScrollArea()
        # self.gr_ctrl_tab_layout.addWidget(self.gr_ctrl_scroll_area)
        self.al_toolbar.addTab(self.gr_ctrl_tab, _("Control"))

        # GRBL Sender TAB
        self.gr_send_tab = QtWidgets.QWidget()
        self.gr_send_tab.setObjectName("connect_tab")
        self.gr_send_tab_layout = QtWidgets.QVBoxLayout(self.gr_send_tab)
        self.gr_send_tab_layout.setContentsMargins(2, 2, 2, 2)

        # self.gr_send_scroll_area = VerticalScrollArea()
        # self.gr_send_tab_layout.addWidget(self.gr_send_scroll_area)
        self.al_toolbar.addTab(self.gr_send_tab, _("Sender"))

        for idx in range(self.al_toolbar.count()):
            if self.al_toolbar.tabText(idx) == _("Connect"):
                self.al_toolbar.tabBar.setTabTextColor(idx, QtGui.QColor('red'))
            if self.al_toolbar.tabText(idx) == _("Control"):
                self.al_toolbar.tabBar.setTabEnabled(idx, False)
            if self.al_toolbar.tabText(idx) == _("Sender"):
                self.al_toolbar.tabBar.setTabEnabled(idx, False)
        # #############################################################################################################

        # #############################################################################################################
        # GRBL CONNECT
        # #############################################################################################################
        self.connect_frame = FCFrame()
        self.gr_conn_tab_layout.addWidget(self.connect_frame)

        grbl_conn_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 1, 0])
        self.connect_frame.setLayout(grbl_conn_grid)

        # COM list
        self.com_list_label = FCLabel('%s:' % _("COM list"))
        self.com_list_label.setToolTip(
            _("Lists the available serial ports.")
        )

        self.com_list_combo = FCComboBox()
        self.com_search_button = FCButton(_("Search"))
        self.com_search_button.setToolTip(
            _("Search for the available serial ports.")
        )
        grbl_conn_grid.addWidget(self.com_list_label, 2, 0)
        grbl_conn_grid.addWidget(self.com_list_combo, 2, 1)
        grbl_conn_grid.addWidget(self.com_search_button, 2, 2)

        # BAUDRATES list
        self.baudrates_list_label = FCLabel('%s:' % _("Baud rates"))
        self.baudrates_list_label.setToolTip(
            _("Lists the available serial ports.")
        )

        self.baudrates_list_combo = FCComboBox()
        cb_model = QtCore.QStringListModel()
        self.baudrates_list_combo.setModel(cb_model)
        self.baudrates_list_combo.addItems(
            ['9600', '19200', '38400', '57600', '115200', '230400', '460800', '500000', '576000', '921600', '1000000',
             '1152000', '1500000', '2000000'])
        self.baudrates_list_combo.setCurrentText('115200')

        grbl_conn_grid.addWidget(self.baudrates_list_label, 4, 0)
        grbl_conn_grid.addWidget(self.baudrates_list_combo, 4, 1)

        # New baudrate
        self.new_bd_label = FCLabel('%s:' % _("New"))
        self.new_bd_label.setToolTip(
            _("New, custom baudrate.")
        )

        self.new_baudrate_entry = FCSpinner()
        self.new_baudrate_entry.set_range(40, 9999999)

        self.add_bd_button = FCButton(_("Add"))
        self.add_bd_button.setToolTip(
            _("Add the specified custom baudrate to the list.")
        )
        grbl_conn_grid.addWidget(self.new_bd_label, 6, 0)
        grbl_conn_grid.addWidget(self.new_baudrate_entry, 6, 1)
        grbl_conn_grid.addWidget(self.add_bd_button, 6, 2)

        self.del_bd_button = FCButton(_("Delete selected baudrate"))
        grbl_conn_grid.addWidget(self.del_bd_button, 8, 0, 1, 3)

        ctrl_h_lay = QtWidgets.QHBoxLayout()
        self.controller_reset_button = FCButton(_("Reset"))
        self.controller_reset_button.setToolTip(
            _("Software reset of the controller.")
        )
        self.controller_reset_button.setDisabled(True)
        ctrl_h_lay.addWidget(self.controller_reset_button)

        self.com_connect_button = FCButton()
        self.com_connect_button.setText(_("Disconnected"))
        self.com_connect_button.setToolTip(
            _("Connect to the selected port with the selected baud rate.")
        )
        self.com_connect_button.setStyleSheet("QPushButton {background-color: red;}")
        ctrl_h_lay.addWidget(self.com_connect_button)

        grbl_conn_grid.addWidget(FCLabel(""), 9, 0, 1, 3)
        grbl_conn_grid.setRowStretch(9, 1)
        grbl_conn_grid.addLayout(ctrl_h_lay, 10, 0, 1, 3)

        # #############################################################################################################
        # GRBL CONTROL
        # #############################################################################################################
        self.ctrl_grbl_frame = FCFrame()
        self.gr_ctrl_tab_layout.addWidget(self.ctrl_grbl_frame)
        grbl_ctrl_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 1, 0])
        self.ctrl_grbl_frame.setLayout(grbl_ctrl_grid)

        self.ctrl_grbl_frame2 = FCFrame()
        self.gr_ctrl_tab_layout.addWidget(self.ctrl_grbl_frame2)
        grbl_ctrl2_grid = GLay(v_spacing=5, h_spacing=3)
        self.ctrl_grbl_frame2.setLayout(grbl_ctrl2_grid)

        self.gr_ctrl_tab_layout.addStretch(1)

        jog_title_label = FCLabel(_("Jog"), bold=True)

        zero_title_label = FCLabel(_("Zero Axes"), bold=True)
        # zero_title_label.setStyleSheet("""
        #                         FCLabel
        #                         {
        #                             font-weight: bold;
        #                         }
        #                         """)

        grbl_ctrl_grid.addWidget(jog_title_label, 0, 0)
        grbl_ctrl_grid.addWidget(zero_title_label, 0, 2)

        self.jog_wdg = FCJog(self.app)
        self.jog_wdg.setStyleSheet("""
                            FCJog
                            {
                                border: 1px solid lightgray;
                                border-radius: 5px;
                            }
                            """)

        self.zero_axs_wdg = FCZeroAxes(self.app)
        self.zero_axs_wdg.setStyleSheet("""
                            FCZeroAxes
                            {
                                border: 1px solid lightgray;
                                border-radius: 5px
                            }
                            """)
        grbl_ctrl_grid.addWidget(self.jog_wdg, 2, 0)
        grbl_ctrl_grid.addWidget(self.zero_axs_wdg, 2, 2)

        self.pause_resume_button = RotatedToolButton()
        self.pause_resume_button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum,
                                               QtWidgets.QSizePolicy.Policy.Expanding)
        self.pause_resume_button.setText(_("Pause/Resume"))
        self.pause_resume_button.setCheckable(True)
        self.pause_resume_button.setStyleSheet("""
                            RotatedToolButton:checked
                            {
                                background-color: red;
                                color: white;
                                border: none;
                            }
                            """)

        pause_frame = QtWidgets.QFrame()
        pause_frame.setContentsMargins(0, 0, 0, 0)
        pause_frame.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Expanding)
        pause_h_lay = QtWidgets.QHBoxLayout()
        pause_h_lay.setContentsMargins(0, 0, 0, 0)

        pause_h_lay.addWidget(self.pause_resume_button)
        pause_frame.setLayout(pause_h_lay)
        grbl_ctrl_grid.addWidget(pause_frame, 2, 1)

        # JOG Step
        self.jog_step_label = FCLabel('%s:' % _("Step"))
        self.jog_step_label.setToolTip(
            _("Each jog action will move the axes with this value.")
        )

        self.jog_step_entry = FCSliderWithDoubleSpinner()
        self.jog_step_entry.set_precision(self.decimals)
        self.jog_step_entry.setSingleStep(0.1)
        self.jog_step_entry.set_range(0, 500)

        grbl_ctrl2_grid.addWidget(self.jog_step_label, 0, 0)
        grbl_ctrl2_grid.addWidget(self.jog_step_entry, 0, 1)

        # JOG Feedrate
        self.jog_fr_label = FCLabel('%s:' % _("Feedrate"))
        self.jog_fr_label.setToolTip(
            _("Feedrate when jogging.")
        )

        self.jog_fr_entry = FCSliderWithDoubleSpinner()
        self.jog_fr_entry.set_precision(self.decimals)
        self.jog_fr_entry.setSingleStep(10)
        self.jog_fr_entry.set_range(0, 10000)

        grbl_ctrl2_grid.addWidget(self.jog_fr_label, 1, 0)
        grbl_ctrl2_grid.addWidget(self.jog_fr_entry, 1, 1)

        # #############################################################################################################
        # GRBL SENDER
        # #############################################################################################################
        self.sender_frame = FCFrame()
        self.gr_send_tab_layout.addWidget(self.sender_frame)

        grbl_send_grid = GLay(v_spacing=5, h_spacing=3, c_stretch=[1, 0])
        self.sender_frame.setLayout(grbl_send_grid)

        # Send CUSTOM COMMAND
        self.grbl_command_label = FCLabel('%s:' % _("Send Command"))
        self.grbl_command_label.setToolTip(
            _("Send a custom command to GRBL.")
        )
        grbl_send_grid.addWidget(self.grbl_command_label, 2, 0, 1, 2)

        self.grbl_command_entry = FCEntry()
        self.grbl_command_entry.setPlaceholderText(_("Type GRBL command ..."))

        self.grbl_send_button = QtWidgets.QToolButton()
        self.grbl_send_button.setText(_("Send"))
        self.grbl_send_button.setToolTip(
            _("Send a custom command to GRBL.")
        )
        grbl_send_grid.addWidget(self.grbl_command_entry, 4, 0)
        grbl_send_grid.addWidget(self.grbl_send_button, 4, 1)

        # Get Parameter
        self.grbl_get_param_label = FCLabel('%s:' % _("Get Config parameter"))
        self.grbl_get_param_label.setToolTip(
            _("A GRBL configuration parameter.")
        )
        grbl_send_grid.addWidget(self.grbl_get_param_label, 6, 0, 1, 2)

        self.grbl_parameter_entry = FCEntry()
        self.grbl_parameter_entry.setPlaceholderText(_("Type GRBL parameter ..."))

        self.grbl_get_param_button = QtWidgets.QToolButton()
        self.grbl_get_param_button.setText(_("Get"))
        self.grbl_get_param_button.setToolTip(
            _("Get the value of a specified GRBL parameter.")
        )
        grbl_send_grid.addWidget(self.grbl_parameter_entry, 8, 0)
        grbl_send_grid.addWidget(self.grbl_get_param_button, 8, 1)

        grbl_send_grid.setRowStretch(9, 1)

        # GET Report
        self.grbl_report_button = FCButton(_("Get Report"))
        self.grbl_report_button.setToolTip(
            _("Print in shell the GRBL report.")
        )
        grbl_send_grid.addWidget(self.grbl_report_button, 10, 0, 1, 2)

        hm_lay = QtWidgets.QHBoxLayout()
        # GET HEIGHT MAP
        self.grbl_get_heightmap_button = FCButton(_("Apply AutoLevelling"))
        self.grbl_get_heightmap_button.setToolTip(
            _("Will send the probing GCode to the GRBL controller,\n"
              "wait for the Z probing data and then apply this data\n"
              "over the original GCode therefore doing autolevelling.")
        )
        hm_lay.addWidget(self.grbl_get_heightmap_button, stretch=1)

        # STOP
        self.stop_probing_button = FCButton(_("Stop"))
        self.stop_probing_button.setToolTip(
            _("Stop the running GRBL operation now.\n"
              "GRBL goes into feed hold.\n"
              "Press Reset, then Home, before more motion.")
        )
        self.stop_probing_button.setStyleSheet("QPushButton {background-color: red; color: white;}")
        hm_lay.addWidget(self.stop_probing_button)

        self.grbl_save_height_map_button = QtWidgets.QToolButton()
        self.grbl_save_height_map_button.setIcon(QtGui.QIcon(self.app.resource_location + '/save_as.png'))
        self.grbl_save_height_map_button.setToolTip(
            _("Will save the GRBL height map.")
        )
        hm_lay.addWidget(self.grbl_save_height_map_button, stretch=0, alignment=Qt.AlignmentFlag.AlignRight)

        grbl_send_grid.addLayout(hm_lay, 12, 0, 1, 2)

        self.grbl_frame.hide()
        # #############################################################################################################

        height_lay = QtWidgets.QHBoxLayout()
        self.h_gcode_button = FCButton(_("Save Probing GCode"))
        self.h_gcode_button.setToolTip(
            _("Will save the probing GCode.")
        )
        self.h_gcode_button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                          QtWidgets.QSizePolicy.Policy.MinimumExpanding)

        height_lay.addWidget(self.h_gcode_button)
        self.view_h_gcode_button = QtWidgets.QToolButton()
        self.view_h_gcode_button.setIcon(QtGui.QIcon(self.app.resource_location + '/edit_file32.png'))
        # self.view_h_gcode_button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
        #                                        QtWidgets.QSizePolicy.Policy.Ignored)
        self.view_h_gcode_button.setToolTip(
            _("View/Edit the probing GCode.")
        )
        # height_lay.addStretch()
        height_lay.addWidget(self.view_h_gcode_button)

        self.al_box.addLayout(height_lay)

        self.import_heights_button = FCButton(_("Import Height Map"))
        self.import_heights_button.setToolTip(
            _("Import the file that has the Z heights\n"
              "obtained through probing and then apply this data\n"
              "over the original GCode therefore\n"
              "doing autolevelling.")
        )
        self.al_box.addWidget(self.import_heights_button)

        # self.h_gcode_button.hide()
        # self.import_heights_button.hide()

        # separator_line = QtWidgets.QFrame()
        # separator_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        # separator_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        # grid0.addWidget(separator_line, 35, 0, 1, 2)

        self.tools_box.addStretch(1)

        # ## Reset Tool
        self.reset_button = FCButton(_("Reset Tool"), bold=True)
        self.reset_button.setIcon(QtGui.QIcon(self.app.resource_location + '/reset32.png'))
        self.reset_button.setToolTip(
            _("Will reset the tool parameters.")
        )
        self.tools_box.addWidget(self.reset_button)
        # ############################ FINISHED GUI ###################################
        # #############################################################################

        GLay.set_common_column_size([tool_grid, param_grid], 0)

        self.plot_probing_pts_cb.stateChanged.connect(self.on_plot_points_changed)
        self.avoid_exc_holes_cb.stateChanged.connect(self.on_avoid_exc_holes_changed)

    def confirmation_message(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%.*f, %.*f]' % (_("Edited value is out of range"),
                                                                                  self.decimals,
                                                                                  minval,
                                                                                  self.decimals,
                                                                                  maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def confirmation_message_int(self, accepted, minval, maxval):
        if accepted is False:
            self.app.inform[str, bool].emit('[WARNING_NOTCL] %s: [%d, %d]' %
                                            (_("Edited value is out of range"), minval, maxval), False)
        else:
            self.app.inform[str, bool].emit('[success] %s' % _("Edited value is within limits."), False)

    def on_plot_points_changed(self, state):
        self.app.options["tools_al_plot_points"] = False if not state else True

    def on_avoid_exc_holes_changed(self, state):
        self.app.options["tools_al_avoid_exc_holes"] = False if not state else True
