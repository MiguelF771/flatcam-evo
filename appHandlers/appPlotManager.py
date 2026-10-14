from PyQt6 import QtCore, QtGui, QtWidgets

from appObjects.ObjectCollection import CNCJobObject
from appObjects.ObjectDispatch import dispatch_attr
from appGUI.GUIElements import FCInputDialogSlider
from appCommon.Common import color_variant
from copy import deepcopy
import builtins
import gettext

if '_' not in builtins.__dict__:
    _ = gettext.gettext


class AppPlotManager(QtCore.QObject):
    """Handler for plot visibility and rendering."""

    def __init__(self, app):
        super().__init__()
        self.app = app

        # Facades for App attributes used by handler methods
        self.log = app.log
        self.inform = app.inform
        self.options = app.options
        self.collection = app.collection
        self.worker_task = app.worker_task
        self.plotcanvas = app.plotcanvas
        self.clear_pool = app.clear_pool
        self.app_obj = app.app_obj
        self.defaults = app.defaults
        self.proc_container = app.proc_container
        self.use_3d_engine = app.use_3d_engine

    def _get_gerber_color_list(self):
        color_list = self.options.get(
            "gerber_color_list",
            self.defaults.get("gerber_color_list")
        )
        if "gerber_color_list" not in self.options:
            color_list = deepcopy(color_list)
            self.options["gerber_color_list"] = color_list
        return color_list

    def on_plots_updated(self):
        """
        Callback used to report when the plots have changed.
        Adjust axes and zooms to fit.

        :return: None
        """
        self.plotcanvas.update() if self.use_3d_engine else self.plotcanvas.auto_adjust_axes()
        self.app.on_zoom_fit()
        self.collection.update_view()

    def on_toolbar_replot(self):
        """
        Callback for toolbar button. Re-plots all objects.

        :return: None
        """

        try:
            obj = self.collection.get_active()
            obj.read_form() if obj else self.app.on_zoom_fit()
        except Exception as e:
            self.log.debug("on_toolbar_replot() -> %s" % str(e))

        self.plot_all()

    def disable_other_plots(self):
        self.defaults.report_usage("disable_other_plots()")

        self.disable_plots(self.collection.get_non_selected())
        self.inform.emit('[success] %s' % _("All non selected plots disabled."))

    def enable_other_plots(self):
        self.defaults.report_usage("enable_other_plots()")

        self.enable_plots(self.collection.get_non_selected())
        self.inform.emit('[success] %s' % _("All non selected plots enabled."))

    def on_enable_sel_plots(self, silent=False):
        if silent is False:
            self.log.debug("App.on_enable_sel_plots()")
        object_list = self.collection.get_selected()
        self.enable_plots(objects=object_list, silent=silent)
        if silent is False:
            self.inform.emit('[success] %s' % _("Selected plots enabled..."))

    def on_disable_sel_plots(self):
        self.log.debug("App.on_disable_sel_plot()")

        # self.inform.emit(_("Disabling plots ..."))
        object_list = self.collection.get_selected()
        self.disable_plots(objects=object_list)
        self.inform.emit('[success] %s' % _("Selected plots disabled..."))

    def disable_all_plots(self):
        self.defaults.report_usage("disable_all_plots()")

        self.disable_plots(self.collection.get_list())
        self.inform.emit('[success] %s' % _("All plots disabled."))

    def enable_all_plots(self):
        self.defaults.report_usage("enable_all_plots()")

        self.enable_plots(self.collection.get_list())
        self.inform.emit('[success] %s' % _("All plots enabled."))

    def enable_plots(self, objects, silent=False):
        """
        Enable plots

        :param objects: list of Objects to be enabled
        :param silent: If True there are no messages from this method
        :return:
        """
        if silent is False:
            self.log.debug("Enabling plots ...")
        # self.inform.emit('%s...' % _("Working"))

        for obj in objects:
            if obj.obj_options['plot'] is False:
                obj.obj_options.set_change_callback(lambda x: None)
                try:
                    obj.obj_options['plot'] = True
                    obj.ui.plot_cb.stateChanged.disconnect(obj.on_plot_cb_click)
                    # disable this cb while disconnected,
                    # in case the operation takes time the user is not allowed to change it
                    obj.ui.plot_cb.setDisabled(True)
                except AttributeError:
                    # try to build the ui
                    obj.build_ui()
                    # and try again
                    self.enable_plots(objects)
                    return

                obj.set_form_item("plot")
                try:
                    obj.ui.plot_cb.stateChanged.connect(obj.on_plot_cb_click)
                    obj.ui.plot_cb.setDisabled(False)
                except AttributeError:
                    # try to build the ui
                    obj.build_ui()
                    # and try again
                    self.enable_plots(objects)
                    return
                obj.obj_options.set_change_callback(obj.on_options_change)
        self.collection.update_view()

        def worker_task(objs):
            with self.proc_container.new(_("Enabling plots ...")):
                for plot_obj in objs:
                    # obj.obj_options['plot'] = True
                    # only change the visibility if the shapes of the last full plot are still on the canvas
                    if isinstance(plot_obj, CNCJobObject):
                        kind = self.options.get("cncjob_plot_kind", self.defaults.get("cncjob_plot_kind"))
                        if not plot_obj.show_plotted_shapes(True, kind=kind):
                            plot_obj.plot(visible=True, kind=kind)
                    elif not plot_obj.show_plotted_shapes(True):
                        plot_obj.plot(visible=True)

        self.worker_task.emit({'fcn': worker_task, 'params': [objects]})

    def disable_plots(self, objects):
        """
        Disables plots

        :param objects: list of Objects to be disabled
        :return:
        """

        self.log.debug("Disabling plots ...")
        # self.inform.emit('%s...' % _("Working"))

        for obj in objects:
            if obj.obj_options['plot'] is True:
                obj.obj_options.set_change_callback(lambda x: None)
                try:
                    obj.obj_options['plot'] = False
                    obj.ui.plot_cb.stateChanged.disconnect(obj.on_plot_cb_click)
                    obj.ui.plot_cb.setDisabled(True)
                except (AttributeError, TypeError):
                    # try to build the ui
                    obj.build_ui()
                    # and try again
                    self.disable_plots(objects)
                    return

                obj.set_form_item("plot")
                try:
                    obj.ui.plot_cb.stateChanged.connect(obj.on_plot_cb_click)
                    obj.ui.plot_cb.setDisabled(False)
                except (AttributeError, TypeError):
                    # try to build the ui
                    obj.build_ui()
                    # and try again
                    self.disable_plots(objects)
                    return
                obj.obj_options.set_change_callback(obj.on_options_change)

        try:
            self.app.delete_selection_shape()
        except Exception as e:
            self.log.error("App.disable_plots() --> %s" % str(e))

        self.collection.update_view()

        def worker_task(objs):
            with self.proc_container.new(_("Disabling plots ...")):
                for plot_obj in objs:
                    # obj.obj_options['plot'] = True
                    # only change the visibility if the shapes of the last full plot are still on the canvas
                    if isinstance(plot_obj, CNCJobObject):
                        kind = self.options.get("cncjob_plot_kind", self.defaults.get("cncjob_plot_kind"))
                        if not plot_obj.show_plotted_shapes(False, kind=kind):
                            plot_obj.plot(visible=False, kind=kind)
                    elif not plot_obj.show_plotted_shapes(False):
                        plot_obj.plot(visible=False)

        self.worker_task.emit({'fcn': worker_task, 'params': [objects]})

    def toggle_plots(self, objects):
        """
        Toggle plots visibility

        :param objects:     list of Objects for which to be toggled the visibility
        :return:            None
        """

        # if no objects selected then do nothing
        if not self.collection.get_selected():
            return

        self.log.debug("Toggling plots ...")
        # self.inform.emit('%s...' % _("Working"))
        for obj in objects:
            if obj.obj_options['plot'] is False:
                obj.obj_options['plot'] = True
            else:
                obj.obj_options['plot'] = False
        try:
            self.app.delete_selection_shape()
        except Exception:
            pass
        self.app_obj.plots_updated.emit()

    def clear_plots(self):
        """
        Clear the plots

        :return:            None
        """

        objects = self.collection.get_list()

        for obj in objects:
            obj.clear(obj == objects[-1])

        # Clear pool to free memory
        self.clear_pool()

    def gerber_redraw(self):
        # the Gerber redraw should work only if there is only one object of type Gerber and active in the selection
        sel_gerb_objs = [o for o in self.collection.get_selected() if o.kind == 'gerber' and o.obj_options['plot']]
        if len(sel_gerb_objs) > 1:
            return

        obj = self.collection.get_active()
        if not obj or (obj.obj_options['plot'] is False or obj.kind != 'gerber'):
            # we don't replot something that is disabled or if it is not Gerber type
            return

        def worker_task(plot_obj):
            # change only the draw order; a new plot of a big object takes a long time
            if not plot_obj.bring_to_top():
                plot_obj.plot(visible=True)

        self.worker_task.emit({'fcn': worker_task, 'params': [obj]})

    def plot_all(self, fit_view=True, muted=False, use_thread=True):
        """
        Re-generates all plots from all objects.

        :param fit_view:    if True will plot the objects and will adjust the zoom to fit all plotted objects into view
        :param muted:       if True don't print messages
        :param use_thread:  if True will use threading for plotting the objects
        :return:            None
        """
        self.log.debug("Plot_all()")
        obj_collection = self.collection.get_list()
        if not obj_collection:
            return

        if muted is not True:
            self.inform[str, bool].emit('%s...' % _("Redrawing all objects"), False)

        for plot_obj in obj_collection:
            if plot_obj.obj_options['plot'] is False:
                continue

            def worker_task(obj):
                with self.proc_container.new("Plotting"):
                    if obj.kind == 'cncjob':
                        try:
                            dia = obj.ui.tooldia_entry.get_value()
                        except AttributeError:
                            dia = self.options.get("cncjob_tooldia", self.defaults.get("cncjob_tooldia"))
                        obj.plot(kind=self.options.get("cncjob_plot_kind", self.defaults.get("cncjob_plot_kind")), dia=dia)
                    else:
                        obj.plot()
                    if fit_view is True:
                        self.app_obj.object_plotted.emit(obj)

            if use_thread is True:
                # Send to worker
                self.worker_task.emit({'fcn': worker_task, 'params': [plot_obj]})
            else:
                worker_task(plot_obj)

    def on_set_color_action_triggered(self):
        """
        This slot gets called by clicking on the menu entry in the Set Color submenu of the context menu in Project Tab

        :return:
        """

        new_color = self.options.get('gerber_plot_fill', self.defaults.get('gerber_plot_fill'))
        new_line_color = self.options.get('gerber_plot_line', self.defaults.get('gerber_plot_line'))

        clicked_action = self.sender()

        assert isinstance(clicked_action, QtGui.QAction), "Expected a QAction, got %s" % type(clicked_action)
        act_name = clicked_action.text()
        sel_obj_list = self.collection.get_selected()

        if not sel_obj_list:
            return

        # a default value, I just chose this one
        alpha_level = 'BF'
        for sel_obj in sel_obj_list:
            if hasattr(sel_obj, "alpha_level"):
                alpha_level = sel_obj.alpha_level
            else:
                alpha_option = dispatch_attr(sel_obj, 'alpha_fill_option')
                fixed_alpha = dispatch_attr(sel_obj, 'default_alpha_level')
                if alpha_option:
                    alpha_level = str(hex(int(self.options.get(alpha_option, self.defaults.get(alpha_option))[7:9], 16))[2:])
                elif fixed_alpha is not None:
                    alpha_level = fixed_alpha
                else:
                    self.log.debug(
                        "App.on_set_color_action_triggered() --> Default transparency level "
                        "for this object type not supported yet")
                    continue
                sel_obj.alpha_level = alpha_level

        if act_name == _('Red'):
            new_color = '#FF0000' + alpha_level
        if act_name == _('Blue'):
            new_color = '#0000FF' + alpha_level

        if act_name == _('Yellow'):
            new_color = '#FFDF00' + alpha_level
        if act_name == _('Green'):
            new_color = '#00FF00' + alpha_level
        if act_name == _('Purple'):
            new_color = '#FF00FF' + alpha_level
        if act_name == _('Brown'):
            new_color = '#A52A2A' + alpha_level
        if act_name == _('Indigo'):
            new_color = '#4B0082' + alpha_level
        if act_name == _('White'):
            new_color = '#FFFFFF' + alpha_level
        if act_name == _('Black'):
            new_color = '#000000' + alpha_level

        # selection of a custom color will open a QColor dialog
        if act_name == _('Custom'):
            new_color = QtGui.QColor(self.options.get('gerber_plot_fill', self.defaults.get('gerber_plot_fill'))[:7])
            c_dialog = QtWidgets.QColorDialog()
            plot_fill_color = c_dialog.getColor(initial=new_color)

            if plot_fill_color.isValid() is False:
                return

            new_color = str(plot_fill_color.name()) + alpha_level

        # when it is desired the return to the default color set in Preferences
        if act_name == _("Default"):
            for sel_obj in sel_obj_list:
                fill_option = dispatch_attr(sel_obj, 'plot_default_fill_option')
                line_option = dispatch_attr(sel_obj, 'plot_default_line_option')
                if fill_option and line_option:
                    new_color = self.options.get(fill_option, self.defaults.get(fill_option))
                    new_line_color = self.options.get(line_option, self.defaults.get(line_option))
                else:
                    self.log.debug(
                        "App.on_set_color_action_triggered() --> Default color for this object type not supported yet")
                    continue

                sel_obj.fill_color = new_color
                sel_obj.outline_color = new_line_color
                sel_obj.shapes.redraw(
                    update_colors=(new_color, new_line_color)
                )

            self.set_obj_color_in_preferences_dict(sel_obj_list, new_color, new_line_color)
            return

        # set of a custom transparency level
        if act_name == _("Opacity"):
            # alpha_level, ok_button = QtWidgets.QInputDialog.getInt(self.ui, _("Set alpha level ..."),
            #                                                        '%s:' % _("Value"),
            #                                                        min=0, max=255, step=1, value=191)

            alpha_dialog = FCInputDialogSlider(
                self.app.ui, _("Set alpha level ..."), '%s:' % _("Value"), min=0, max=255, step=1, init_val=191)
            alpha_level, ok_button = alpha_dialog.get_results()

            if ok_button:
                group = self.collection.group_items["gerber"]
                group_index = self.collection.index(group.row(), 0, QtCore.QModelIndex())

                alpha_str = format(alpha_level, '02x') if alpha_level != 0 else '00'

                for sel_obj in sel_obj_list:
                    new_color = sel_obj.fill_color[:-2] + alpha_str
                    new_line_color = sel_obj.outline_color
                    sel_obj.alpha_level = alpha_str
                    sel_obj.fill_color = new_color
                    sel_obj.shapes.redraw(update_colors=(new_color, new_line_color))

                    if sel_obj.kind == 'gerber':
                        item = sel_obj.item
                        item_index = self.collection.index(item.row(), 0, group_index)
                        idx = item_index.row()
                        new_c = (new_line_color, new_color, '%s_%d' % (_("Layer"), int(idx + 1)))
                        try:
                            self._get_gerber_color_list()[idx] = new_c
                        except Exception as err_msg:
                            self.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
                            self.log.error(str(err_msg))
            return

        new_line_color = color_variant(new_color[:7], 0.7)
        if act_name == _("White"):
            new_line_color = color_variant("#dedede", 0.7)

        for sel_obj in sel_obj_list:
            if dispatch_attr(sel_obj, 'has_plot_colors'):
                sel_obj.fill_color = new_color
                sel_obj.outline_color = new_line_color

                sel_obj.shapes.redraw(
                    update_colors=(new_color, new_line_color)
                )

        self.set_obj_color_in_preferences_dict(sel_obj_list, new_color, new_line_color)

    def set_obj_color_in_preferences_dict(self, list_of_obj, fill_color, outline_color):
        """
        Will save the set colors into a list that will be used next time when Gerber objects are loaded.
        First loaded Gerber will have the first color in the list, second loaded Gerber object will have set the second
        color in the list and so on.

        :param list_of_obj:         a list of App objects that are currently loaded and selected
        :type list_of_obj:          list
        :param fill_color:          the fill color that will be set for the selected objects
        :type fill_color:           str
        :param outline_color:       the outline color that will be set for the selected objects
        :type outline_color:        str
        :return:
        :rtype:
        """

        # make sure to set the color in the Gerber colors storage self.options["gerber_color_list"]
        group_gerber = self.collection.group_items["gerber"]
        group_gerber_index = self.collection.index(group_gerber.row(), 0, QtCore.QModelIndex())
        all_gerber_list = [x for x in self.collection.get_list() if x.kind == 'gerber']

        for sel_obj in list_of_obj:
            if sel_obj.kind == 'gerber':
                item = sel_obj.item
                item_index = self.collection.index(item.row(), 0, group_gerber_index)
                idx = item_index.row()
                new_c = (outline_color, fill_color, '%s_%d' % (_("Layer"), int(idx + 1)))
                gerber_color_list = self._get_gerber_color_list()
                try:
                    gerber_color_list[idx] = new_c
                except IndexError:
                    for x in range(len(gerber_color_list), len(all_gerber_list)):
                        gerber_color_list.append(
                            (
                                self.options.get('gerber_plot_fill', self.defaults.get('gerber_plot_fill')),  # content color
                                self.options.get('gerber_plot_line', self.defaults.get('gerber_plot_line')),  # outline color
                                '%s_%d' % (_("Layer"), int(idx + 1)))  # layer name
                        )
                    gerber_color_list[idx] = new_c
            elif sel_obj.kind == 'excellon':
                new_c = (outline_color, fill_color)
                self.options["excellon_color"] = new_c
