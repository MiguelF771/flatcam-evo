# ###########################################################
# FlatCAM: 2D Post-processing for Manufacturing             #
# http://flatcam.org                                        #
# Author: Juan Pablo Caram (c)                              #
# Date: 2/5/2014                                            #
# MIT Licence                                               #
# Modified by Marius Stanciu (2019)                          #
# ###########################################################

from PyQt6 import QtGui, QtWidgets, QtCore
from PyQt6.QtCore import QPoint

from copy import deepcopy
import numpy as np

import builtins
import gettext

if '_' not in builtins.__dict__:
    _ = gettext.gettext


class AppObjectOps(QtCore.QObject):
    """Handler for object operations: transforms (flip, rotate, skew), copy, delete, move."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.log = app.log
        self.inform = app.inform
        self.options = app.options
        self.defaults = app.defaults
        self.ui = app.ui
        # Handler-local state for mouse event tracking
        self._mp_zc = None

    def on_flipy(self):
        """
        Executed when the menu entry in Options -> Flip on Y axis is clicked.

        :return:
        """
        self.defaults.report_usage("on_flipy()")

        obj_list = self.app.collection.get_selected()
        xminlist = []
        yminlist = []
        xmaxlist = []
        ymaxlist = []

        if not obj_list:
            self.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
        else:
            try:
                # first get a bounding box to fit all
                for obj in obj_list:
                    xmin, ymin, xmax, ymax = obj.bounds()
                    xminlist.append(xmin)
                    yminlist.append(ymin)
                    xmaxlist.append(xmax)
                    ymaxlist.append(ymax)

                # get the minimum x,y and maximum x,y for all objects selected
                xminimal = min(xminlist)
                yminimal = min(yminlist)
                xmaximal = max(xmaxlist)
                ymaximal = max(ymaxlist)

                px = 0.5 * (xminimal + xmaximal)
                py = 0.5 * (yminimal + ymaximal)

                # execute mirroring
                for obj in obj_list:
                    obj.mirror('X', [px, py])
                    obj.plot()
                    self.app.app_obj.object_changed.emit(obj)
                self.inform.emit('[success] %s.' % _("Flip on Y axis done"))

            except Exception as e:
                self.inform.emit('[ERROR_NOTCL] %s: %s.' % (_("Action was not executed"), str(e)))
                return

    def on_flipx(self):
        """
        Executed when the menu entry in Options -> Flip on X axis is clicked.

        :return:
        """
        self.defaults.report_usage("on_flipx()")

        obj_list = self.app.collection.get_selected()
        xminlist = []
        yminlist = []
        xmaxlist = []
        ymaxlist = []

        if not obj_list:
            self.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
        else:
            try:
                # first get a bounding box to fit all
                for obj in obj_list:
                    xmin, ymin, xmax, ymax = obj.bounds()
                    xminlist.append(xmin)
                    yminlist.append(ymin)
                    xmaxlist.append(xmax)
                    ymaxlist.append(ymax)

                # get the minimum x,y and maximum x,y for all objects selected
                xminimal = min(xminlist)
                yminimal = min(yminlist)
                xmaximal = max(xmaxlist)
                ymaximal = max(ymaxlist)

                px = 0.5 * (xminimal + xmaximal)
                py = 0.5 * (yminimal + ymaximal)

                # execute mirroring
                for obj in obj_list:
                    obj.mirror('Y', [px, py])
                    obj.plot()
                    self.app.app_obj.object_changed.emit(obj)
                self.inform.emit('[success] %s.' % _("Flip on X axis done"))

            except Exception as e:
                self.inform.emit('[ERROR_NOTCL] %s: %s.' % (_("Action was not executed"), str(e)))
                return

    def on_rotate(self, silent=False, preset=None):
        """
        Executed when Options -> Rotate Selection menu entry is clicked.

        :param silent:  If silent is True then use the preset value for the angle of the rotation.
        :param preset:  A value to be used as predefined angle for rotation.
        :return:
        """
        self.defaults.report_usage("on_rotate()")

        obj_list = self.app.collection.get_selected()
        xminlist = []
        yminlist = []
        xmaxlist = []
        ymaxlist = []

        if not obj_list:
            self.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
        else:
            if silent is False:
                from appGUI.GUIElements import FCInputDoubleSpinner
                rotatebox = FCInputDoubleSpinner(title=_("Transform"), text=_("Enter the Angle value:"),
                                                 min=-360, max=360, decimals=4,
                                                 init_val=float(self.options.get('tools_transform_rotate', self.defaults.get('tools_transform_rotate', 90))),
                                                 parent=self.ui)
                rotatebox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/rotate.png'))
                num, ok = rotatebox.get_value()
            else:
                num = preset
                ok = True

            if ok:
                try:
                    # first get a bounding box to fit all
                    for obj in obj_list:
                        xmin, ymin, xmax, ymax = obj.bounds()
                        xminlist.append(xmin)
                        yminlist.append(ymin)
                        xmaxlist.append(xmax)
                        ymaxlist.append(ymax)

                    # get the minimum x,y and maximum x,y for all objects selected
                    xminimal = min(xminlist)
                    yminimal = min(yminlist)
                    xmaximal = max(xmaxlist)
                    ymaximal = max(ymaxlist)
                    px = 0.5 * (xminimal + xmaximal)
                    py = 0.5 * (yminimal + ymaximal)

                    for sel_obj in obj_list:
                        sel_obj.rotate(-float(num), point=(px, py))
                        sel_obj.plot()
                        self.app.app_obj.object_changed.emit(sel_obj)
                    self.inform.emit('[success] %s' % _("Rotation done."))
                except Exception as e:
                    self.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Rotation movement was not executed."), str(e)))
                    return

    def on_skewx(self):
        """
        Executed when the menu entry in Options -> Skew on X axis is clicked.

        :return:
        """
        self.defaults.report_usage("on_skewx()")

        obj_list = self.app.collection.get_selected()
        xminlist = []
        yminlist = []

        if not obj_list:
            self.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
        else:
            from appGUI.GUIElements import FCInputDoubleSpinner
            skewxbox = FCInputDoubleSpinner(title=_("Transform"), text=_("Enter the Angle value:"),
                                            min=-360, max=360, decimals=4,
                                            init_val=float(self.options.get('tools_transform_skew_x', self.defaults.get('tools_transform_skew_x', 0.0))),
                                            parent=self.ui)
            skewxbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/skewX.png'))
            num, ok = skewxbox.get_value()
            if ok:
                # first get a bounding box to fit all
                for obj in obj_list:
                    xmin, ymin, xmax, ymax = obj.bounds()
                    xminlist.append(xmin)
                    yminlist.append(ymin)

                # get the minimum x,y for all objects selected
                xminimal = min(xminlist)
                yminimal = min(yminlist)

                for obj in obj_list:
                    obj.skew(num, 0, point=(xminimal, yminimal))

                    # make sure to update the Offset field in Properties Tab
                    try:
                        obj.set_offset_values()
                    except AttributeError:
                        pass

                    obj.plot()
                    self.app.app_obj.object_changed.emit(obj)
                self.inform.emit('[success] %s' % _("Skew on X axis done."))

    def on_skewy(self):
        """
        Executed when the menu entry in Options -> Skew on Y axis is clicked.

        :return:
        """
        self.defaults.report_usage("on_skewy()")

        obj_list = self.app.collection.get_selected()
        xminlist = []
        yminlist = []

        if not obj_list:
            self.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
        else:
            from appGUI.GUIElements import FCInputDoubleSpinner
            skewybox = FCInputDoubleSpinner(title=_("Transform"), text=_("Enter the Angle value:"),
                                            min=-360, max=360, decimals=4,
                                            init_val=float(self.options.get('tools_transform_skew_y', self.defaults.get('tools_transform_skew_y', 0.0))),
                                            parent=self.ui)
            skewybox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/skewY.png'))
            num, ok = skewybox.get_value()
            if ok:
                # first get a bounding box to fit all
                for obj in obj_list:
                    xmin, ymin, xmax, ymax = obj.bounds()
                    xminlist.append(xmin)
                    yminlist.append(ymin)

                # get the minimum x,y for all objects selected
                xminimal = min(xminlist)
                yminimal = min(yminlist)

                for obj in obj_list:
                    obj.skew(0, num, point=(xminimal, yminimal))

                    # make sure to update the Offset field in Properties Tab
                    try:
                        obj.set_offset_values()
                    except AttributeError:
                        pass

                    obj.plot()
                    self.app.app_obj.object_changed.emit(obj)
                self.inform.emit('[success] %s' % _("Skew on Y axis done."))

    def on_set_origin(self):
        """
        Set the origin to the left mouse click position

        :return: None
        """
        self.defaults.report_usage("on_set_origin()")

        self.inform.emit(_('Click to set the origin ...'))
        self.app.inhibit_context_menu = True

        def plotcanvas_fit_view(_):
            self.app.plotcanvas.fit_view()

        self.app.connect_custom_signal(plotcanvas_fit_view, object)

        def origin_replot(_list=None):
            def worker_task():
                with self.app.proc_container.new('%s...' % _("Plotting")):
                    for obj in self.app.collection.get_list():
                        obj.plot()
                    self.app.custom_signal.emit(None)
                if self.app.use_3d_engine:
                    self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_set_zero_click)
                else:
                    self.app.plotcanvas.graph_event_disconnect(self._mp_zc)
                self.app.inhibit_context_menu = False

            self.app.worker_task.emit({'fcn': worker_task, 'params': []})

        self._mp_zc = self.app.plotcanvas.graph_event_connect('mouse_release', self.on_set_zero_click)

        # first disconnect it as it may have been used by something else
        try:
            self.app.replot_signal.disconnect()
        except TypeError:
            pass
        self.app.replot_signal[list].connect(origin_replot)

    def on_set_zero_click(self, event, location=None, noplot=False, use_thread=True):
        """
        :param event:
        :param location:
        :param noplot:
        :param use_thread:
        :return:
        """
        noplot_sig = noplot
        right_button = 2 if self.app.use_3d_engine else 3

        def worker_task(app_obj):
            with app_obj.proc_container.new(_("Setting Origin...")):
                obj_list = app_obj.collection.get_list()

                for obj in obj_list:
                    obj.offset((x, y))
                    app_obj.app_obj.object_changed.emit(obj)

                    # Update the object bounding box options
                    a, b, c, d = obj.bounds()
                    obj.obj_options['xmin'] = a
                    obj.obj_options['ymin'] = b
                    obj.obj_options['xmax'] = c
                    obj.obj_options['ymax'] = d

                    # make sure to update the Offset field in Properties Tab
                    try:
                        obj.set_offset_values()
                    except AttributeError:
                        pass

                app_obj.inform.emit('[success] %s...' % _('Origin set'))

                # update the source_file container with the new offset code
                for obj in obj_list:
                    out_name = obj.obj_options["name"]

                    if obj.kind == 'gerber':
                        obj.source_file = app_obj.f_handlers.export_gerber(
                            obj_name=out_name, filename=None, local_use=obj, use_thread=False)

                    elif obj.kind == 'excellon':
                        obj.source_file = app_obj.f_handlers.export_excellon(
                            obj_name=out_name, filename=None, local_use=obj, use_thread=False)

                    elif obj.kind == 'geometry':
                        obj.source_file = app_obj.f_handlers.export_dxf(
                            obj_name=out_name, filename=None, local_use=obj, use_thread=False)

                if noplot_sig is False:
                    app_obj.replot_signal.emit([])

        if location is not None:
            if len(location) != 2:
                self.inform.emit('[ERROR_NOTCL] %s...' % _("Origin coordinates specified but incomplete."))
                return 'fail'

            x, y = location

            if use_thread is True:
                self.app.worker_task.emit({'fcn': worker_task, 'params': [self.app]})
            else:
                worker_task(self.app)

            self.app.should_we_save = True
            return

        if event is not None and event.button == 1:
            event_pos = event.pos if self.app.use_3d_engine else (event.xdata, event.ydata)

            pos_canvas = self.app.plotcanvas.translate_coords(event_pos)

            if self.app.grid_status():
                pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
            else:
                pos = pos_canvas

            x = 0 - pos[0]
            y = 0 - pos[1]

            if use_thread is True:
                self.app.worker_task.emit({'fcn': worker_task, 'params': [self.app]})
            else:
                worker_task(self.app)

            self.app.should_we_save = True
        elif event is not None and event.button == right_button:
            if self.ui.popMenu.mouse_is_panning is False:
                if self.app.use_3d_engine:
                    self.app.plotcanvas.graph_event_disconnect('mouse_release', self.on_set_zero_click)
                    self.app.inhibit_context_menu = False
                else:
                    self.app.plotcanvas.graph_event_disconnect(self._mp_zc)

                self.inform.emit('[WARNING_NOTCL] %s' % _("Cancelled."))

    def on_move2origin(self, use_thread=True):
        """
        Move selected objects to origin.
        :param use_thread: Control if to use threaded operation. Boolean.
        :return:
        """
        def worker_task():
            with self.app.proc_container.new(_("Moving to Origin...")):
                obj_list = self.app.collection.get_selected()

                if not obj_list:
                    self.inform.emit('[ERROR_NOTCL] %s' % _("Failed. No object(s) selected..."))
                    return

                xminlist = []
                yminlist = []

                # first get a bounding box to fit all
                for obj in obj_list:
                    xmin, ymin, xmax, ymax = obj.bounds()
                    xminlist.append(xmin)
                    yminlist.append(ymin)

                # get the minimum x,y for all objects selected
                x = min(xminlist)
                y = min(yminlist)

                for obj in obj_list:
                    obj.offset((-x, -y))
                    self.app.app_obj.object_changed.emit(obj)

                    # Update the object bounding box options
                    a, b, c, d = obj.bounds()
                    obj.obj_options['xmin'] = a
                    obj.obj_options['ymin'] = b
                    obj.obj_options['xmax'] = c
                    obj.obj_options['ymax'] = d

                    # make sure to update the Offset field in Properties Tab
                    try:
                        obj.set_offset_values()
                    except AttributeError:
                        pass

                for obj in obj_list:
                    obj.plot()
                self.app.plotcanvas.fit_view()

                for obj in obj_list:
                    out_name = obj.obj_options["name"]

                    if obj.kind == 'gerber':
                        obj.source_file = self.app.f_handlers.export_gerber(
                            obj_name=out_name, filename=None, local_use=obj, use_thread=False)

                    elif obj.kind == 'excellon':
                        obj.source_file = self.app.f_handlers.export_excellon(
                            obj_name=out_name, filename=None, local_use=obj, use_thread=False)

                    elif obj.kind == 'geometry':
                        obj.source_file = self.app.f_handlers.export_dxf(
                            obj_name=out_name, filename=None, local_use=obj, use_thread=False)

                self.inform.emit('[success] %s...' % _('Origin set'))

        if use_thread is True:
            self.app.worker_task.emit({'fcn': worker_task, 'params': []})
        else:
            worker_task()
        self.app.should_we_save = True

    def on_jump_to(self, custom_location=None, fit_center=True):
        """
        Jump to a location by setting the mouse cursor location.

        :param custom_location:     Jump to a specified point. (x, y) tuple.
        :param fit_center:          If to fit view. Boolean.
        :return:
        """
        from appGUI.GUIElements import DialogBoxRadio

        self.defaults.report_usage("on_jump_to()")

        if not custom_location:
            dia_box_location = None

            try:
                dia_box_location = eval(self.app.clipboard.text())
            except Exception:
                pass

            if isinstance(dia_box_location, tuple):
                dia_box_location = str(dia_box_location)
            else:
                dia_box_location = None

            dia_box = DialogBoxRadio(title=_("Jump to ..."),
                                     label=_("Enter the coordinates in format X,Y:"),
                                     icon=QtGui.QIcon(self.app.resource_location + '/jump_to32.png'),
                                     initial_text=dia_box_location,
                                     reference=self.options.get('global_jump_ref', self.defaults.get('global_jump_ref', 'abs')),
                                     parent=self.ui)

            if dia_box.ok is True:
                try:
                    location = eval(dia_box.location)

                    if not isinstance(location, tuple):
                        self.inform.emit(_("Wrong coordinates. Enter coordinates in format: X,Y"))
                        return

                    if dia_box.reference == 'rel':
                        rel_x = self.app.mouse_pos[0] + location[0]
                        rel_y = self.app.mouse_pos[1] + location[1]
                        location = (rel_x, rel_y)
                    self.options['global_jump_ref'] = dia_box.reference
                except Exception:
                    return
            else:
                return
        else:
            location = custom_location

        self.app.jump_signal.emit(location)

        if fit_center:
            self.app.plotcanvas.fit_center(loc=location)

        cursor = QtGui.QCursor()

        if self.app.use_3d_engine:
            cal_location = (location[0], location[1])
            canvas_origin = self.app.plotcanvas.native.mapToGlobal(QPoint(0, 0))
            jump_loc = self.app.plotcanvas.translate_coords_2((cal_location[0], cal_location[1]))
            j_pos = (
                int(canvas_origin.x() + round(jump_loc[0])),
                int(canvas_origin.y() + round(jump_loc[1]))
            )
            cursor.setPos(j_pos[0], j_pos[1])
        else:
            # find the canvas origin which is in the top left corner
            canvas_origin = self.app.plotcanvas.native.mapToGlobal(QPoint(0, 0))

            # determine the coordinates for the lowest left point of the canvas
            x0, y0 = canvas_origin.x(), canvas_origin.y() + self.ui.right_layout.geometry().height()

            # transform the given location from data coordinates to display coordinates
            loc = self.app.plotcanvas.axes.transData.transform_point(location)
            j_pos = (
                int(x0 + loc[0]),
                int(y0 - loc[1])
            )
            cursor.setPos(j_pos[0], j_pos[1])
            self.app.plotcanvas.mouse = [location[0], location[1]]
            if self.options.get("global_cursor_color_enabled", self.defaults.get("global_cursor_color_enabled", True)) is True:
                self.app.plotcanvas.draw_cursor(x_pos=location[0], y_pos=location[1], color=self.app.cursor_color_3D)
            else:
                self.app.plotcanvas.draw_cursor(x_pos=location[0], y_pos=location[1])

        if self.app.grid_status():
            # Update cursor
            self.app.app_cursor.set_data(np.asarray([(location[0], location[1])]),
                                     symbol='++', edge_color=self.app.plotcanvas.cursor_color,
                                     edge_width=self.options.get("global_cursor_width", self.defaults.get("global_cursor_width", 2)),
                                     size=self.options.get("global_cursor_size", self.defaults.get("global_cursor_size", 20)))

        # Set the relative position label
        self.app.dx = location[0] - float(self.app.rel_point1[0])
        self.app.dy = location[1] - float(self.app.rel_point1[1])

        self.ui.update_location_labels(self.app.dx, self.app.dy, location[0], location[1])
        self.app.plotcanvas.on_update_text_hud(self.app.dx, self.app.dy, location[0], location[1])

        self.inform.emit('[success] %s' % _("Done."))
        return location

    def on_locate(self, obj, fit_center=True):
        """
        Jump to one of the corners (or center) of an object by setting the mouse cursor location

        :param obj:         The object on which to locate certain points
        :param fit_center:  If to fit view. Boolean.
        :return:            A point location. (x, y) tuple.
        """
        from appGUI.GUIElements import DialogBoxChoice

        self.defaults.report_usage("on_locate()")

        if obj is None:
            self.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
            return 'fail'

        choices = [
            {"label": _("T Left"), "value": "tl"},
            {"label": _("T Right"), "value": "tr"},
            {"label": _("B Left"), "value": "bl"},
            {"label": _("B Right"), "value": "br"},
            {"label": _("Center"), "value": "c"}
        ]
        dia_box = DialogBoxChoice(title=_("Locate ..."),
                                  icon=QtGui.QIcon(self.app.resource_location + '/locate16.png'),
                                  choices=choices,
                                  default_choice=self.options.get('global_locate_pt', self.defaults.get('global_locate_pt', 'bl')),
                                  parent=self.ui)

        if dia_box.ok is True:
            try:
                location_point = dia_box.location_point
                self.options['global_locate_pt'] = dia_box.location_point
            except Exception:
                return
        else:
            return

        loc_b = obj.bounds()
        if location_point == 'bl':
            location = (loc_b[0], loc_b[1])
        elif location_point == 'tl':
            location = (loc_b[0], loc_b[3])
        elif location_point == 'br':
            location = (loc_b[2], loc_b[1])
        elif location_point == 'tr':
            location = (loc_b[2], loc_b[3])
        else:
            # center
            cx = loc_b[0] + abs((loc_b[2] - loc_b[0]) / 2)
            cy = loc_b[1] + abs((loc_b[3] - loc_b[1]) / 2)
            location = (cx, cy)

        self.app.locate_signal.emit(location, location_point)

        if fit_center:
            self.app.plotcanvas.fit_center(loc=location)

        cursor = QtGui.QCursor()

        if self.app.use_3d_engine:
            cal_location = (location[0], location[1])
            canvas_origin = self.app.plotcanvas.native.mapToGlobal(QPoint(0, 0))
            jump_loc = self.app.plotcanvas.translate_coords_2((cal_location[0], cal_location[1]))
            j_pos = (
                int(canvas_origin.x() + round(jump_loc[0])),
                int(canvas_origin.y() + round(jump_loc[1]))
            )
            cursor.setPos(j_pos[0], j_pos[1])
        else:
            # find the canvas origin which is in the top left corner
            canvas_origin = self.app.plotcanvas.native.mapToGlobal(QPoint(0, 0))

            # determine the coordinates for the lowest left point of the canvas
            x0, y0 = canvas_origin.x(), canvas_origin.y() + self.ui.right_layout.geometry().height()

            # transform the given location from data coordinates to display coordinates
            loc = self.app.plotcanvas.axes.transData.transform_point(location)
            j_pos = (
                int(x0 + loc[0]),
                int(y0 - loc[1])
            )
            cursor.setPos(j_pos[0], j_pos[1])
            self.app.plotcanvas.mouse = [location[0], location[1]]
            if self.options.get("global_cursor_color_enabled", self.defaults.get("global_cursor_color_enabled", True)) is True:
                self.app.plotcanvas.draw_cursor(x_pos=location[0], y_pos=location[1], color=self.app.cursor_color_3D)
            else:
                self.app.plotcanvas.draw_cursor(x_pos=location[0], y_pos=location[1])

        if self.app.grid_status():
            # Update cursor
            self.app.app_cursor.set_data(np.asarray([(location[0], location[1])]),
                                     symbol='++', edge_color=self.app.plotcanvas.cursor_color,
                                     edge_width=self.options.get("global_cursor_width", self.defaults.get("global_cursor_width", 2)),
                                     size=self.options.get("global_cursor_size", self.defaults.get("global_cursor_size", 20)))

        # Set the relative position label
        self.app.dx = location[0] - float(self.app.rel_point1[0])
        self.app.dy = location[1] - float(self.app.rel_point1[1])

        self.ui.update_location_labels(self.app.dx, self.app.dy, location[0], location[1])
        self.app.plotcanvas.on_update_text_hud(self.app.dx, self.app.dy, location[0], location[1])

        self.inform.emit('[success] %s' % _("Done."))
        return location

    def on_numeric_move(self, val=None):
        """
        Move to a specific location (absolute or relative against current position)

        :param val: custom offset value, (x, y)
        :type val:  tuple
        :return:    None
        :rtype:     None
        """
        from appGUI.GUIElements import DialogBoxRadio
        from shapely.geometry import MultiPolygon, MultiLineString

        # move only the objects selected and plotted and visible
        obj_list = [
            obj for obj in self.app.collection.get_selected() if obj.obj_options['plot'] and obj.visible is True
        ]

        if not obj_list:
            self.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed."), _("Nothing selected.")))
            return

        def bounds_rec(obj_or_geo):
            try:
                minx = float('inf')
                miny = float('inf')
                maxx = float('-inf')
                maxy = float('-inf')

                work_geo = obj_or_geo.geoms if isinstance(obj_or_geo, (MultiPolygon, MultiLineString)) else obj_or_geo
                for k in work_geo:
                    minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                    minx = min(minx, minx_)
                    miny = min(miny, miny_)
                    maxx = max(maxx, maxx_)
                    maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            except TypeError:
                # it's an App object, return its bounds
                if obj_or_geo:
                    return obj_or_geo.bounds()
                return 0, 0, 0, 0

        bounds = bounds_rec(obj_list)

        if not val:
            dia_box_location = (0.0, 0.0)

            dia_box = DialogBoxRadio(title=_("Move to ..."),
                                     label=_("Enter the coordinates in format X,Y:"),
                                     icon=QtGui.QIcon(self.app.resource_location + '/move32_bis.png'),
                                     initial_text=dia_box_location,
                                     reference=self.options.get('global_move_ref', self.defaults.get('global_move_ref', 'abs')),
                                     parent=self.ui)

            if dia_box.ok is True:
                try:
                    location = [float(x) if x != '' else 0.0 for x in dia_box.location.split(',')]
                    if not isinstance(location, (tuple, list)):
                        self.inform.emit(_("Wrong coordinates. Enter coordinates in format: X,Y"))
                        return

                    if dia_box.reference == 'abs':
                        abs_x = location[0] - bounds[0]
                        abs_y = location[1] - bounds[1]
                        location = (abs_x, abs_y)
                    self.options['global_jump_ref'] = dia_box.reference
                except Exception:
                    return
            else:
                return
        else:
            location = val

        self.app.move_tool.move_handler(offset=location, objects=obj_list)

    def on_copy_command(self):
        """
        Will copy a selection of objects, creating new objects.
        :return:
        """
        self.defaults.report_usage("on_copy_command()")

        for obj in self.app.collection.get_selected():
            obj_name = obj.obj_options["name"]

            def initialize(obj_init, app_obj):
                obj_init.solid_geometry = deepcopy(obj.solid_geometry)
                try:
                    obj_init.follow_geometry = deepcopy(obj.follow_geometry)
                except AttributeError:
                    pass
                try:
                    if obj.tools:
                        obj_init.tools = deepcopy(obj.tools)
                except Exception as cerr:
                    app_obj.log.error("App.on_copy_command() --> %s" % str(cerr))
                    return "fail"
                try:
                    obj_init.source_file = deepcopy(obj.source_file)
                except (AttributeError, TypeError):
                    pass
                if obj.kind == 'gerber':
                    obj_init.export_as_regions = getattr(obj, 'export_as_regions', False)

            def initialize_excellon(obj_init, app_obj):
                obj_init.source_file = deepcopy(obj.source_file)
                obj_init.tools = deepcopy(obj.tools)
                obj_init.create_geometry()
                if not obj_init.tools:
                    app_obj.log.debug("on_copy_command() --> no excellon tools")
                    return 'fail'

            def initialize_script(new_obj, app_obj):
                app_obj.log.debug("Script copied.")
                new_obj.source_file = deepcopy(obj.source_file)

            def initialize_document(new_obj, app_obj):
                app_obj.log.debug("Document copied.")
                new_obj.source_file = deepcopy(obj.source_file)

            try:
                if obj.kind == 'excellon':
                    self.app.app_obj.new_object("excellon", str(obj_name) + "_copy", initialize_excellon)
                elif obj.kind == 'gerber':
                    self.app.app_obj.new_object("gerber", str(obj_name) + "_copy", initialize)
                elif obj.kind == 'geometry':
                    self.app.app_obj.new_object("geometry", str(obj_name) + "_copy", initialize)
                elif obj.kind == 'script':
                    self.app.app_obj.new_object("script", str(obj_name) + "_copy", initialize_script)
                elif obj.kind == 'document':
                    self.app.app_obj.new_object("document", str(obj_name) + "_copy", initialize_document)
                elif obj.kind == 'cncjob':
                    self.log.warning("on_copy_command() --> CNCJob objects cannot be copied.")
                    self.inform.emit('[WARNING_NOTCL] %s' % _("CNCJob objects cannot be copied."))
            except Exception as e:
                self.log.error("Copy operation failed: %s for object: %s" % (str(e), str(obj_name)))

    def on_copy_object2(self, custom_name):
        for obj in self.app.collection.get_selected():
            obj_name = obj.obj_options["name"]
            outname = str(obj_name) + custom_name

            def initialize_geometry(obj_init, app_obj):
                obj_init.solid_geometry = deepcopy(obj.solid_geometry)
                try:
                    obj_init.follow_geometry = deepcopy(obj.follow_geometry)
                except AttributeError:
                    pass
                try:
                    obj_init.tools = deepcopy(obj.tools)
                except AttributeError:
                    pass
                try:
                    if obj.tools:
                        obj_init.tools = deepcopy(obj.tools)
                except Exception as ee:
                    app_obj.log.error("on_copy_object2() --> %s" % str(ee))

            def initialize_gerber(obj_init, app_obj):
                obj_init.solid_geometry = deepcopy(obj.solid_geometry)
                obj_init.tools = deepcopy(obj.tools)
                obj_init.aperture_macros = deepcopy(obj.aperture_macros)
                obj_init.export_as_regions = getattr(obj, 'export_as_regions', False)
                if not obj_init.tools:
                    app_obj.log.debug("on_copy_object2() --> no gerber apertures")
                    return 'fail'

            def initialize_excellon(new_obj, app_obj):
                new_obj.tools = deepcopy(obj.tools)
                new_obj.create_geometry()
                if not new_obj.tools:
                    app_obj.log.debug("on_copy_object2() --> no excellon tools")
                    return 'fail'
                new_obj.source_file = app_obj.f_handlers.export_excellon(
                    obj_name=outname, local_use=new_obj, filename=None, use_thread=False)

            try:
                from appObjects.ObjectCollection import ExcellonObject, GerberObject, GeometryObject
                if isinstance(obj, ExcellonObject):
                    self.app.app_obj.new_object("excellon", outname, initialize_excellon)
                elif isinstance(obj, GerberObject):
                    self.app.app_obj.new_object("gerber", outname, initialize_gerber)
                elif isinstance(obj, GeometryObject):
                    self.app.app_obj.new_object("geometry", outname, initialize_geometry)
            except Exception as er:
                return "Operation failed: %s" % str(er)

    def on_rename_object(self, text):
        """
        Will rename an object.

        :param text:    New name for the object.
        :return:
        """
        self.defaults.report_usage("on_rename_object()")

        named_obj = self.app.collection.get_active()
        if named_obj is not None:
            try:
                named_obj.obj_options['name'] = text
            except Exception as e:
                self.log.error("App.on_rename_object() --> Could not rename: %s" % str(e))

    def on_delete_keypress(self):
        current_widget = self.ui.notebook.currentWidget()
        if current_widget is None:
            return
        notebook_widget_name = current_widget.objectName()

        # work only if the notebook tab on focus is the properties_tab and only if the object is Geometry
        if notebook_widget_name == 'properties_tab':
            active = self.app.collection.get_active()
            if active is not None and active.kind == 'geometry':
                active.on_tool_delete()

        # work only if the notebook tab on focus is the Tools_Tab
        elif notebook_widget_name == 'plugin_tab':
            tool_widget = self.ui.plugin_scroll_area.widget().objectName()

            # and only if the tool is NCC Plugin
            if self.app.ncclear_tool is not None and tool_widget == self.app.ncclear_tool.pluginName:
                self.app.ncclear_tool.on_tool_delete()

            # and only if the tool is Paint Plugin
            elif self.app.paint_tool is not None and tool_widget == self.app.paint_tool.pluginName:
                self.app.paint_tool.on_tool_delete()

            # and only if the tool is Solder Paste Dispensing Plugin
            elif self.app.paste_tool is not None and tool_widget == self.app.paste_tool.pluginName:
                self.app.paste_tool.on_tool_delete()

            # and only if the tool is Isolation Plugin
            elif self.app.isolation_tool is not None and tool_widget == self.app.isolation_tool.pluginName:
                self.app.isolation_tool.on_tool_delete()
        else:
            self.on_delete()

    def on_delete(self, force_deletion=False):
        """
        Delete the currently selected FlatCAMObjs.

        :param force_deletion:  used by Tcl command
        :return: None
        """
        self.defaults.report_usage("on_delete()")

        response = None
        bt_ok = None

        if self.app.call_source == 'app':
            if self.options.get("global_delete_confirmation", self.defaults.get("global_delete_confirmation", True)) is True and force_deletion is False:
                from appGUI.GUIElements import FCMessageBox
                msgbox = FCMessageBox(parent=self.ui)
                title = _("Delete objects")
                txt = _("Are you sure you want to permanently delete\n"
                        "the selected objects?")
                msgbox.setWindowTitle(title)
                msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
                msgbox.setText('<b>%s</b>' % title)
                msgbox.setInformativeText(txt)
                msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/deleteshape32.png'))

                bt_ok = msgbox.addButton(_('Ok'), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
                msgbox.addButton(_('Cancel'), QtWidgets.QMessageBox.ButtonRole.RejectRole)

                msgbox.setDefaultButton(bt_ok)
                msgbox.exec()
                response = msgbox.clickedButton()

            if self.options.get("global_delete_confirmation", self.defaults.get("global_delete_confirmation", True)) is False or force_deletion is True:
                response = bt_ok

            if response == bt_ok:
                if self.app.collection.get_active():
                    self.log.debug("App.on_delete()")

                    for obj_active in self.app.collection.get_selected():
                        if obj_active.kind == 'gerber':
                            obj_active.mark_shapes_storage.clear()
                            obj_active.mark_shapes.clear(update=True)
                            obj_active.mark_shapes.enabled = False
                            if self.app.tool_shapes is not None:
                                self.app.tool_shapes.clear(update=True)

                        elif obj_active.kind == 'cncjob':
                            try:
                                obj_active.text_col.enabled = False
                                del obj_active.text_col
                                obj_active.annotation.clear(update=True)
                                del obj_active.annotation
                                obj_active.probing_shapes.clear(update=True)
                            except AttributeError as e:
                                self.log.debug(
                                    "App.on_delete() --> CNCJob object: %s. %s" % (str(obj_active.obj_options['name']),
                                                                       str(e))
                                )

                    selected = list(self.app.collection.get_selected())
                    for ob in selected:
                        self.delete_first_selected(ob)

                    # make sure that the selection shape is deleted, too
                    self.app.delete_selection_shape()

                    # if there are no longer objects delete also the exclusion areas shapes
                    if not self.app.collection.get_list():
                        self.app.exc_areas.clear_shapes()
                else:
                    self.inform.emit('[ERROR_NOTCL] %s %s' % (_("Failed."), _("No object is selected.")))
        else:
            self.inform.emit(_("Save the work in Editor and try again ..."))

    def delete_first_selected(self, del_obj=None):
        # Keep this for later
        try:
            if del_obj is not None:
                sel_obj = del_obj
            else:
                sel_obj = self.app.collection.get_active()

            name = sel_obj.obj_options["name"]
            isPlotted = sel_obj.obj_options["plot"]
        except AttributeError:
            self.log.debug("Nothing selected for deletion")
            return

        if self.app.use_3d_engine is False:
            # Remove plot only if the object was plotted otherwise will fail
            if isPlotted:
                try:
                    self.app.plotcanvas.figure.delaxes(sel_obj.shapes.axes)
                except Exception as e:
                    self.log.error("App.delete_first_selected() --> %s" % str(e))

            self.app.plotcanvas.auto_adjust_axes()

        # Remove from dictionary
        self.app.collection.delete_active()

        # Clear form
        self.app.setup_default_properties_tab()

        self.inform.emit('%s: %s' % (_("Object deleted"), name))

    def on_selectall(self):
        """
        Will draw a selection box shape around the selected objects.

        :return:
        """
        # delete the possible selection box around a possible selected object
        self.app.delete_selection_shape()
        for name in self.app.collection.get_names():
            self.app.collection.set_active(name)
            curr_sel_obj = self.app.collection.get_by_name(name)
            # create the selection box around the selected object
            if self.options.get('global_selection_shape', self.defaults.get('global_selection_shape', True)) is True:
                try:
                    self.app.draw_selection_shape(curr_sel_obj)
                except Exception as gerr:
                    self.log.error(
                        "App.on_select_all(). Object %s can't be selected on canvas. Error: %s" % (name, str(gerr)))

    def on_deselect_all(self):
        self.app.collection.set_all_inactive()
        self.app.delete_selection_shape()

    def on_copy_name(self):
        self.defaults.report_usage("on_copy_name()")

        obj = self.app.collection.get_active()
        try:
            name = obj.obj_options["name"]
        except AttributeError:
            self.log.debug("on_copy_name() --> No object selected to copy it's name")
            self.inform.emit('[WARNING_NOTCL] %s' % _("No object is selected."))
            return

        self.app.clipboard.setText(name)
        self.inform.emit(_("Name copied to clipboard ..."))
