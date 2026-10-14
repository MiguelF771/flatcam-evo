from tclCommands.TclCommand import TclCommand
from appObjects.GerberObject import GerberObject

import collections


class TclCommandJoinGerber(TclCommand):
    """
    Tcl shell command to merge Gerber objects.

    example:

    """

    # List of all command aliases, to be able use old names for backward compatibility
    aliases = ['join_gerber', 'join_gerbers']

    description = '%s %s' % ("--", "Merge two or more Gerber objects and create "
                                   "a new Gerber object with them.")

    # Dictionary of types from Tcl command, needs to be ordered
    arg_names = collections.OrderedDict([

    ])

    # Dictionary of types from Tcl command, needs to be ordered , this  is  for options  like -optionname value
    option_types = collections.OrderedDict([
        ('outname', str),
    ])

    # array of mandatory options for current Tcl command: required = {'name','outname'}
    required = []

    # structured help for current command, args needs to be ordered
    help = {
        'main': "Runs a merge operation (join) on the Gerber objects.\n"
                "The names of the Gerber objects to be merged will be entered after the command,\n"
                "separated by spaces. See the example below.\n"
                "WARNING: if the name of a Gerber object has spaces, enclose the name with quotes.",
        'args': collections.OrderedDict([
            ('outname', 'Name of the new Gerber Object made by joining of other Gerber objects.\n'
                        'If not used then it will be used a generic name: "joined_gerber"'),
        ]),
        'examples': ['join_gerbers grb_name_1 "grb name_2" -outname merged_new_gerber',
                     'join_gerber grb_name_1 "grb name_2"']
    }

    def execute(self, args, unnamed_args):
        """

        :param args:
        :param unnamed_args:
        :return:
        """
        self.app.log.debug("TclCommandJoinGerber.execute()")

        outname = args['outname'] if 'outname' in args else "joined_gerber"
        obj_names = unnamed_args

        if not obj_names:
            self.app.log.error("Missing objects to be joined. Exiting.")
            return "fail"

        objs = []
        for obj_n in obj_names:
            obj = self.app.collection.get_by_name(str(obj_n))
            if obj is None:
                self.app.log.error("Object not found: %s" % obj_n)
                return "fail"
            if not isinstance(obj, GerberObject):
                self.app.log.error("Not a Gerber object: %s" % obj_n)
                return "fail"
            objs.append(obj)

        def initialize(obj_, app):
            GerberObject.merge(grb_list=objs, grb_final=obj_, log=self.app.log)

        if len(objs) >= 2:
            self.app.app_obj.new_object("gerber", outname, initialize, plot=False)
        else:
            self.app.log.error(
                "No Gerber objects to be joined or less than two Gerber objects specified for merging.")
            return "fail"
