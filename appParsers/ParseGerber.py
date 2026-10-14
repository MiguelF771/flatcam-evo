
from typing import Literal, Sequence, cast
from PyQt6 import QtWidgets
from camlib import Geometry, arc, arc_angle, ApertureMacro, grace, flatten_shapely_geometry, translate_geometry, \
    map_geometry

from appParsers.clipper_fuse import fuse_parts
from appParsers.ParseDXF import getdxfgeo
from appParsers.ParseSVG import svgparselength, getsvggeo, svgparse_viewbox

import numpy as np
import traceback
from copy import deepcopy
from typing import Dict, List, Optional, Tuple, Any

from shapely.ops import unary_union, linemerge
import shapely
import shapely.affinity as affinity
from shapely import box as shply_box
from shapely import LinearRing, MultiLineString, LineString, Polygon, MultiPolygon, Point, prepare, is_prepared, simplify
from shapely import STRtree
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry

from lxml import etree as ET
import ezdxf
from ezdxf import recover
import logging
import re
import sys

import gettext
import builtins

if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class Gerber(Geometry):
    """
    Here it is done all the Gerber parsing.

    **ATTRIBUTES**

    * ``tools`` (dict): The keys are names/identifiers of each aperture.
      The values are dictionaries key/value pairs which describe the aperture. The
      type key is always present and the rest depend on the key:

    +-----------+-----------------------------------+
    | Key       | Value                             |
    +===========+===================================+
    | type      | (str) "C", "R", "O", "P", or "AP" |
    +-----------+-----------------------------------+
    | others    | Depend on ``type``                |
    +-----------+-----------------------------------+
    | geometry      | (list)                  |
    +-----------+-----------------------------------+
    * ``aperture_macros`` (dictionary): Are predefined geometrical structures
      that can be instantiated with different parameters in an aperture
      definition. See ``apertures`` above. The key is the name of the macro,
      and the macro itself, the value, is a ``Aperture_Macro`` object.

    * ``flash_geometry`` (list): List of (Shapely) geometric object resulting
      from ``flashes``. These are generated from ``flashes`` in ``do_flashes()``.

    * ``buffered_paths`` (list): List of (Shapely) polygons resulting from
      *buffering* (or thickening) the ``paths`` with the aperture. These are
      generated from ``paths`` in ``buffer_paths()``.

    **USAGE**::

        g = Gerber()
        g.parse_file(filename)
        g.create_geometry()
        do_something(s.solid_geometry)

    """

    # Type annotations for instance attributes
    tools: Dict[int, Dict[str, Any]]
    aperture_macros: Dict[str, 'ApertureMacro']
    solid_geometry: Any  # Union[Polygon, MultiPolygon, List[Polygon]] - complex type
    follow_geometry: List[Any]
    is_lpc: bool
    source_file: str
    int_digits: int
    frac_digits: int
    gerber_zeros: str
    units: str
    conversion_done: bool
    defective_aperture_detected: bool
    use_buffer_for_union: bool
    steps_per_circle: int
    decimals: int
    ser_attrs: List[str]

    # defaults = {
    #     "steps_per_circle": 128,
    #     "use_buffer_for_union": True
    # }

    def __init__(self, app, steps_per_circle: int | None=None):
        """
        Use ``gerber.parse_files()`` or ``gerber.parse_lines()`` to populate the object from Gerber source.

        :return: Gerber object
        :rtype: Gerber
        """
        if self.__dict__.get('_Gerber_inited'):
            return

        # Import app type (for type hinting) here to avoid circular import.
        from appMain import App

        app = cast(App, app)
        self.app: App = app
        self.multigeo = None

        # How to approximate a circle with lines.
        if steps_per_circle is None:
            self.steps_per_circle = int(self._app_option("gerber_circle_steps", 64))
        else:
            self.steps_per_circle = steps_per_circle
        self.decimals = self.app.decimals

        # Initialize parent
        Geometry.__init__(self, geo_steps_per_circle=self.steps_per_circle, app=self.app)

        # Number format
        self.int_digits = 3
        """Number of integer digits in Gerber numbers. Used during parsing."""

        self.frac_digits = 4
        """Number of fraction digits in Gerber numbers. Used during parsing."""

        self.gerber_zeros: Literal["L", "T"] = self._app_option("gerber_def_zeros")
        """Zeros in Gerber numbers. If 'L' then remove leading zeros, if 'T' remove trailing zeros. Used during parsing.
        """

        # ## Gerber elements # ##
        '''
        tools = {
            aperture_id:{
                'type':string, 
                'size':float, 
                'width':float,
                'height':float,
                'geometry': [],
            }
        }
        tools[aperture_id]['geometry'] list elements are dicts
        dict = {
            'solid': [],
            'follow': [],
            'clear': []
        }
        '''

        # store the file units here:
        self.units: Literal["IN", "MM"] = self._app_option("gerber_def_units")

        # aperture storage
        self.tools: dict[int | None, dict] = {}

        # Aperture Macros
        self.aperture_macros: dict[str, ApertureMacro] = {}

        # will store the Gerber geometry's as solids
        self.solid_geometry: list[BaseGeometry] | BaseGeometry = Polygon()

        # will store the Gerber geometry's as paths
        self.follow_geometry = []

        # made True when the LPC command is encountered in Gerber parsing
        # it allows adding data into the clear_geometry key of the self.tools[aperture] dict
        self.is_lpc: bool = False

        self.source_file: str = ''

        # #############################################################################################################
        # ################################# Parser patterns ###########################################################
        # #############################################################################################################

        # Detect Gerber x2 format
        self.gx2_re = re.compile(r'%TF\.FileFunction.*')

        # FS - Format Specification
        # The format of X and Y must be the same!
        # L-omit leading zeros, T-omit trailing zeros, D-no zero suppression
        # A-absolute notation, I-incremental notation
        self.fmt_re = re.compile(r'%?FS([LTD])?([AI])X(\d)(\d)Y\d\d\*%?$')
        self.fmt_re_alt = re.compile(r'%FS([LTD])?([AI])X(\d)(\d)Y\d\d\*MO(IN|MM)\*%$')
        self.fmt_re_orcad = re.compile(r'(G\d+)*\**%FS([LTD])?([AI]).*X(\d)(\d)Y\d\d\*%$')

        # Mode (IN/MM)
        self.mode_re = re.compile(r'^%?MO(IN|MM)\*%?$')

        # Comment G04|G4
        self.comm_re = re.compile(r'^G0?4(.*)$')

        # AD - Aperture definition
        # Aperture Macro names: Name = [a-zA-Z_.$]{[a-zA-Z_.0-9]+}
        # NOTE: Adding "-" to support output from Upverter.
        self.ad_re = re.compile(r'^%ADD(\d\d+)([a-zA-Z_$.][a-zA-Z0-9_$.\-]*)(?:,(.*))?\*%$')

        # AM - Aperture Macro
        # Beginning of macro (Ends with *%):
        # self.am_re = re.compile(r'^%AM([a-zA-Z0-9]*)\*')

        # Tool change
        # May begin with G54 but that is deprecated
        self.tool_re = re.compile(r'^(?:G54)?D(\d\d+)\*$')

        # G01... - Linear interpolation plus flashes with coordinates
        # Operation code (D0x) missing is deprecated... oh well I will support it.
        # Added \s* to allow optional space after G01 (e.g., "G01 X0Y0D02*")
        self.lin_re = re.compile(r'^(?:G0?(1)\s*)?(?=.*X([+-]?\d+))?(?=.*Y([+-]?\d+))?[XY][^DIJ]*(?:D0?([123]))?\*$')

        # Operation code alone, usually just D03 (Flash)
        # May begin with G55 but that is deprecated
        self.opcode_re = re.compile(r'^(?:G55)?D0?([123])\*$')

        # G02/3... - Circular interpolation with coordinates
        # 2-clockwise, 3-counterclockwise
        # Operation code (D0x) missing is deprecated... oh well I will support it.
        # Optional start with G02 or G03, optional end with D01 or D02 with
        # optional coordinates but at least one in any order.
        # Added \s* to allow optional space after G02/G03
        self.circ_re = re.compile(
            r'^(?:G0?([23])\s*)?(?=.*X([+-]?\d+))?(?=.*Y([+-]?\d+))' +
            r'?(?=.*I([+-]?\d+))?(?=.*J([+-]?\d+))?[XYIJ][^D]*(?:D0([12]))?\*$'
        )

        # G01/2/3 Occurring without coordinates
        # Added \s* to allow optional space after G code
        self.interp_re = re.compile(r'^(?:G0?([123]))\s*\*')

        # Single G74 or multi G75 quadrant for circular interpolation
        self.quad_re = re.compile(r'^G7([45]).*\*$')

        # Region mode on
        # In region mode, D01 starts a region
        # and D02 ends it. A new region can be started again
        # with D01. All contours must be closed before
        # D02 or G37.
        self.regionon_re = re.compile(r'^G36\*$')

        # Region mode off
        # Will end a region and come off region mode.
        # All contours must be closed before D02 or G37.
        self.regionoff_re = re.compile(r'^G37\*$')

        # End of file
        self.eof_re = re.compile(r'^M02\*')

        # IP - Image polarity
        self.pol_re = re.compile(r'^%?IP(POS|NEG)\*%?$')

        # LP - Level polarity
        self.lpol_re = re.compile(r'^%LP([DC])\*%$')

        # Units (OBSOLETE)
        self.units_re = re.compile(r'^G7([01])\*$')

        # Absolute/Relative G90/1 (OBSOLETE)
        self.absrel_re = re.compile(r'^G9([01])\*$')

        # Aperture macros
        self.am1_re = re.compile(r'^%AM([^*]+)\*([^%]+)?(%)?$')
        self.am2_re = re.compile(r'(.*)%$')

        # flag to store if a conversion was done. It is needed because multiple units declarations can be found
        # in a Gerber file (normal or obsolete ones)
        self.conversion_done: bool = False

        # Flag to detect if an aperture is used without definition
        self.defective_aperture_detected: bool = False

        self.use_buffer_for_union = self._app_option("gerber_use_buffer_for_union")

        # Attributes to be included in serialization
        # Always append to it because it carries contents
        # from Geometry.
        # True when the native aperture metadata can no longer be trusted to describe the composed geometry.
        # The export then writes the composed solid_geometry as regions. A project without it loads as False.
        self.export_as_regions: bool = False

        self.ser_attrs += ['int_digits', 'frac_digits', 'aperture_macros', 'source_file', 'export_as_regions']

        self._Gerber_inited = True

    # Aperture types the native Gerber writer can declare (appIO.export_gerber).
    NATIVE_WRITER_APERTURE_TYPES = ('C', 'R', 'O')

    def needs_region_export(self) -> bool:
        """
        Decide if the Gerber export must write the composed solid_geometry as regions instead of
        the native aperture records. The result is True when any of these holds:

        * ``export_as_regions`` is set (the aperture metadata no longer matches the geometry);
        * an aperture is of a type the writer cannot declare (anything but C, R, O; the region
          'aperture' 0 of type 'REG' is written natively, so it does not count);
        * a record holds a non-empty 'clear' geometry. Records have no global order, so the native
          writer, which groups them per aperture, cannot keep a dark -> clear -> dark sequence;
        * a record of an 'R' aperture is not a flash of that aperture. The parser draws a track of an 'R'
          aperture as a box around its end points, and the native writer would flash the aperture at the
          end point instead;
        * the object comes from a PCB document import (``obj_options['pcbdoc_import']``).

        The method only reads the object.

        :return: True if the region export must be used
        """
        if getattr(self, 'export_as_regions', False):
            return True
        obj_options = getattr(self, 'obj_options', None) or {}
        if obj_options.get('pcbdoc_import', False):
            return True
        for apid, tool in (getattr(self, 'tools', None) or {}).items():
            if apid == 0 and tool.get('type') == 'REG':
                pass
            elif tool.get('type') not in Gerber.NATIVE_WRITER_APERTURE_TYPES:
                return True
            for record in tool.get('geometry', ()):
                clear = record.get('clear')
                if clear is not None and not clear.is_empty:
                    return True
                if tool.get('type') == 'R' and not _is_rectangle_flash(tool, record):
                    return True
        return False

    def region_export_tools(self) -> dict:
        """
        Build a tools dictionary that holds the composed ``solid_geometry`` (polarity already applied) as
        region records under the region 'aperture' 0, ready for ``GerberObject.export_gerber``.

        Method: ``feature_access.normalize_polygonal`` (flatten the geometry, repair every part with
        ``make_valid``, keep the polygonal parts, union them), so the result is a valid set of disjoint polygons
        with their holes. The polygons are sorted by the area of their exterior, largest first. The writer draws each polygon as a dark region
        for the exterior, followed by clear regions for its interior rings, then switches back to dark.
        A polygon that lies inside the hole of another one has a smaller exterior, so it is drawn after
        the clear ring and is not erased by it. That keeps holes and nested islands.

        The method does not change the object.

        :return: tools dictionary with 1 'REG' aperture, key 0
        """
        # imported here: appParsers.feature_access imports camlib, like this module
        from appParsers.feature_access import normalize_polygonal
        solid = normalize_polygonal(self.solid_geometry if self.solid_geometry is not None else [])
        polygons = [solid] if isinstance(solid, Polygon) else list(solid.geoms)
        polygons = [p for p in polygons if not p.is_empty]
        polygons.sort(key=lambda poly: Polygon(poly.exterior).area, reverse=True)
        return {0: {'type': 'REG', 'size': 0, 'geometry': [{'solid': poly} for poly in polygons]}}

    def aperture_parse(
        self,
        apertureId: str,
        apertureType: str,
        apParameters: Optional[str]
    ) -> Optional[int]:
        """
        Parse gerber aperture definition into dictionary of apertures.
        The following kinds and their attributes are supported:

        * *Circular (C)*: size (float)
        * *Rectangle (R)*: width (float), height (float)
        * *Obround (O)*: width (float), height (float).
        * *Polygon (P)*: diameter(float), vertices(int), [rotation(float)]
        * *Aperture Macro (AM)*: macro (ApertureMacro), modifiers (list)

        :param apertureId: The Id of the aperture being defined.
        :param apertureType: Type of the aperture.
        :param apParameters: Parameters of the aperture.
        :type apertureId: str
        :type apertureType: str
        :type apParameters: str
        :return: Identifier of the aperture.
        :rtype: str
        """
        if self.app.abort_flag:
            # graceful abort requested by the user
            raise grace

        # Found some Gerber with a leading zero in the aperture id and the
        # referenced it without the zero, so this is a hack to handle that.
        apid = int(apertureId)

        # Could be empty for aperture macros
        paramList = [] if apParameters is None or apParameters == "" else apParameters.split('X')

        if apertureType == "C":  # Circle, example: %ADD11C,0.1*%
            self.tools[apid] = {
                "type": "C",
                "size": float(paramList[0])
            }
            return apid

        if apertureType == "R":  # Rectangle, example: %ADD15R,0.05X0.12*%
            self.tools[apid] = {
                "type": "R",
                "width": float(paramList[0]),
                "height": float(paramList[1]),
                "size": np.sqrt(float(paramList[0]) ** 2 + float(paramList[1]) ** 2)}  # Hack
            return apid

        if apertureType == "O":  # Obround
            self.tools[apid] = {
                "type": "O",
                "width": float(paramList[0]),
                "height": float(paramList[1]),
                "size": np.sqrt(float(paramList[0]) ** 2 + float(paramList[1]) ** 2)}  # Hack
            return apid

        if apertureType == "P":  # Polygon (regular)
            self.tools[apid] = {
                "type": "P",
                "diam": float(paramList[0]),
                "nVertices": int(paramList[1]),
                "size": float(paramList[0])}  # Hack
            if len(paramList) >= 3:
                self.tools[apid]["rotation"] = float(paramList[2])
            return apid

        if apertureType in self.aperture_macros:
            self.tools[apid] = {
                "type": "AM",
                # "size": 0.0,
                "macro": self.aperture_macros[apertureType],
                "modifiers": paramList
            }
            return apid

        self.app.log.warning("Aperture not implemented: %s" % str(apertureType))
        return None

    def parse_file(self, filename: str, follow: bool = False) -> Optional[str]:
        """
        Calls Gerber.parse_lines() with generator of lines
        read from the given file. Will split the lines if multiple
        statements are found in a single original line.

        The following line is split into two::

            G54D11*G36*

        First is ``G54D11*`` and seconds is ``G36*``.

        :param filename:        Gerber file to parse.
        :type filename:         str
        :param follow:          If true, will not create polygons, just lines
                                following the gerber path.
        :type follow:           bool
        :return:                None
        """

        with open(filename, 'r') as gfile:

            read_gfile = gfile.read()

            # clean KiCAD files of garbage
            if '%TF.' in read_gfile or '%TO.' in read_gfile or '%TD' in read_gfile or '%TA' in read_gfile:
                new_gfile = ""
                for line in read_gfile.splitlines():
                    if '%TF.' in line or '%TO.' in line or '%TD' in line or '%TA' in line:
                        continue
                    new_gfile += '%s\n' % line
                split_lines = new_gfile.splitlines()
            else:
                split_lines = read_gfile.splitlines()

            def line_generator():
                for line in split_lines:
                    line = line.strip(' \r\n')
                    while len(line) > 0:

                        # If ends with '%' leave as is.
                        if line[-1] == '%':
                            yield line
                            break

                        # Split after '*' if any.
                        starpos = line.find('*')
                        if starpos > -1:
                            cleanline = line[:starpos + 1]
                            yield cleanline
                            line = line[starpos + 1:]

                        # Otherwise leave as is.
                        else:
                            # yield clean line
                            yield line
                            break

            processed_lines = list(line_generator())
            ret_val = self.parse_lines(processed_lines)

            if ret_val == 'fail':
                return 'fail'
            elif ret_val == "defective":
                return "defective"
            elif ret_val == 'drill':
                return 'drill_gx2'
            else:
                return

    def _fold_layers(self, layers: list) -> BaseGeometry:
        """
        Compose the layers 1 by 1, in file order, starting from the current ``self.solid_geometry``.
        This is the sequential union / difference done at every polarity change.

        :param layers: list of (polarity, parts), polarity is 'D' (dark) or 'C' (clear), parts is a list of geometries
        :return: the composed geometry
        """
        result = self.solid_geometry
        for polarity, parts in layers:
            if polarity == 'D':
                result = result.union(unary_union(parts))
            else:
                result = result.difference(unary_union(parts))
        return result

    def _compose_layers(self, layers: list) -> BaseGeometry:
        """
        Compose the layers of the file: the result is the union of every dark layer D(k) minus the union of all
        the clear layers C(j) with j > k. Each dark layer is processed 1 time, with the clear layers found by a
        STRtree. On a GEOS error or an invalid layer it falls back to _fold_layers().

        :param layers: list of (polarity, parts), polarity is 'D' (dark) or 'C' (clear), parts is a list of geometries
        :return: the composed geometry
        """
        try:
            merged = []
            for polarity, parts in layers:
                layer_geo = unary_union(parts)
                if not layer_geo.is_valid:
                    raise shapely.errors.GEOSException("invalid layer geometry")
                merged.append((polarity, layer_geo))

            clear_polys = []
            clear_index = []
            for idx, (polarity, layer_geo) in enumerate(merged):
                if polarity == 'C' and not layer_geo.is_empty:
                    for poly in shapely.get_parts(layer_geo):
                        clear_polys.append(poly)
                        clear_index.append(idx)
            tree = STRtree(clear_polys) if clear_polys else None
            clear_index = np.array(clear_index, dtype=int)

            pieces = []
            for idx, (polarity, layer_geo) in enumerate(merged):
                if polarity != 'D' or layer_geo.is_empty:
                    continue
                dark_geo = layer_geo
                if tree is not None:
                    hits = tree.query(dark_geo, predicate='intersects')
                    hits = hits[clear_index[hits] > idx]
                    if len(hits) > 0:
                        cutter = unary_union([clear_polys[i] for i in hits])
                        dark_geo = dark_geo.difference(cutter)
                if not dark_geo.is_empty:
                    pieces.append(dark_geo)

            return self.solid_geometry.union(unary_union(pieces))
        except shapely.errors.GEOSException as err:
            self.app.log.debug("Gerber polarity layers: fast composition failed (%s), using the sequential fold." %
                               str(err))
            return self._fold_layers(layers)

    # @profile
    def parse_lines(self, glines: Sequence[str]) -> Optional[str]:
        """
        Main Gerber parser. Reads Gerber and populates ``self.paths``, ``self.tools``,
        ``self.flashes``, ``self.regions`` and ``self.units``.

        :param glines: Gerber code as list of strings, each element being
            one line of the source file.
        :type glines: list
        :return: only errors/warnings
        :rtype: str
        
        ================================================================
        ARCHITECTURE NOTES (2026-03-13)
        ================================================================
        This function uses scattered local variables (not a state class)
        for maximum stability. A ParserState dataclass was attempted but
        reverted after introducing edge-case bugs.
        
        WHY THIS APPROACH:
        - Scattered variables have implicit guards that prevent edge cases
        - A state class would require auditing all 1300+ lines for state access
        - Manual testing cannot catch all edge cases - automated tests needed first
        
        SAFE IMPROVEMENTS ALREADY MADE:
        - _add_path_geometry_to_buffers() - extracts repeated path->geometry pattern
        - _add_flash_to_buffers() - extracts flash geometry creation
        - Type hints on method signatures
        - Section header comments (the ### blocks)
        
        DO NOT refactor state management without:
        1. Comprehensive automated tests (see tests/test_gerber_parser.py)
        2. Full audit of all state interactions
        3. Testing with real Gerber files from multiple EDA tools
        
        See docs/plans/2026-03-13-gerber-parser-state-machine.md for full history.
        ================================================================
        """

        is_excellon_gx2 = False

        # ###############################################################
        # #########  PARSER STATE VARIABLES  ############################
        # #########  Grouped by function for maintainability  ###########
        # ###############################################################
        
        # Coordinates of the current path, each is [x, y]
        path = []

        # this is for temporary storage of solid geometry until it is added to poly_buffer
        geo_s = None

        # this is for temporary storage of follow geometry until it is added to follow_buffer
        geo_f = None

        # Polygons are stored here until there is a change in polarity.
        # Only then they are combined via unary_union and added or
        # subtracted from solid_geometry. This is ~100 times faster than
        # applying a union for every new polygon.
        poly_buffer: list[Polygon] = []

        # Finished layers of equal polarity as (polarity, poly_buffer). A layer ends at every %LP..*% command. They
        # are composed 1 time at the end of the file (see _compose_layers()), not 1 by 1 at every polarity change.
        earlier_layers: list[tuple[str, list]] = []

        # store here the follow geometry
        follow_buffer = []

        last_path_aperture: int | None = None
        current_aperture: int | None | Literal["failure"] = None

        # 1,2 or 3 from "G01", "G02" or "G03"
        current_interpolation_mode : int | None = None

        # 1 or 2 from "D01" or "D02"
        # Note this is to support deprecated Gerber not putting
        # an operation code at the end of every coordinate line.
        current_operation_code = None

        # Current coordinates
        current_x = 0
        current_y = 0

        current_d = None

        # Absolute or Relative/Incremental coordinates
        # Not implemented
        # absolute = True

        # How to interpret circular interpolation: SINGLE or MULTI
        quadrant_mode = None

        # Indicates we are parsing an aperture macro
        current_macro = None

        # Indicates the current polarity: D-Dark, C-Clear
        current_polarity = 'D'

        # If a region is being defined
        making_region: bool = False

        # ### Parsing starts here ## ##
        line_num: int = 0

        s_tol = float(self._app_option("gerber_simp_tolerance"))

        # Cache options lookups for performance
        use_simplification = self._app_option("gerber_simplification")
        use_buffering = self._app_option("gerber_buffering")
        extra_buffering = self._app_option("gerber_extra_buffering")
        clean_apertures = self._app_option("gerber_clean_apertures")

        # Cache steps_per_circle conversion
        steps = int(self.steps_per_circle)

        # Source file accumulation (list + join is faster than string concatenation)
        source_lines = []

        self.app.inform.emit('%s %d %s.' % (_("Gerber processing. Parsing"), len(glines), _("Lines").lower()))
        try:
            for gline in glines:
                if self.app.abort_flag:
                    # graceful abort requested by the user
                    raise grace

                line_num += 1
                source_lines.append(gline)

                # Cleanup #
                gline = gline.strip(' \r\n')
                # self.app.log.debug("Line=%3s %s" % (line_num, gline))

                # ###############################################################
                # ################   Ignored lines   ############################
                # ################     Comments      ############################
                # ###############################################################
                match = self.comm_re.search(gline)
                if match:
                    continue

                # ######################################################################################################
                # ######## Detect GERBER X2 format #####################################################################
                # ######################################################################################################
                match = self.gx2_re.search(gline)
                if match:
                    self.app.log.warning('Gerber X2 format detected !!!')
                    self.app.inform.emit(
                        '[WARNING] %s' % _('Gerber X2 format detected. Parsing may not be done correctly.'))
                    if 'Drill' in gline:
                        self.app.log.warning('Drill file in Gerber X2 format detected !!!')
                        self.app.inform.emit(
                            '[WARNING] %s' % _('Drill file Gerber X2 format detected. '
                                               'Parsing may not be done correctly.'))
                        is_excellon_gx2 = True
                    continue

                # ###############################################################
                # ################  Polarity change #############################
                # ########   Example: %LPD*% or %LPC*%        ###################
                # ########   If polarity changes, creates geometry from current #
                # ########    buffer, then adds or subtracts accordingly.       #
                # ###############################################################
                match = self.lpol_re.search(gline)
                if match:
                    new_polarity = match.group(1)
                    # self.app.log.info("Polarity CHANGE, LPC = %s, poly_buff = %s" % (self.is_lpc, poly_buffer))
                    path_length = len(path)

                    if path_length > 1 and current_polarity != new_polarity:

                        # finish the current path and add it to the storage
                        # --- Buffered ----
                        width = self.tools[last_path_aperture]["size"]

                        geo_dict = {}
                        geo_f = LineString(path)
                        if not geo_f.is_empty:
                            follow_buffer.append(geo_f)
                            geo_dict['follow'] = geo_f

                        geo_s = geo_f.buffer(width / 1.999, steps)
                        if not geo_s.is_empty and geo_s.is_valid:
                            poly_buffer.append(geo_s)

                            if self.is_lpc is True:
                                geo_dict['clear'] = geo_s
                            else:
                                geo_dict['solid'] = geo_s

                        self.tools.setdefault(last_path_aperture, {}).setdefault('geometry', []).append(geo_dict)

                        path = [path[-1]]

                    # the open path above was closed with the old polarity; from here on the new polarity is valid
                    self.is_lpc = True if new_polarity == 'C' else False

                    # --- Close the layer ---
                    # The layer is not applied to solid_geometry here. All layers are composed at the end of the file.
                    if len(poly_buffer) > 0:
                        earlier_layers.append((current_polarity, poly_buffer))
                        poly_buffer = []

                    current_polarity = new_polarity
                    continue

                # ################################################################
                # #####################  Number format ###########################
                # #####################  Example: %FSLAX24Y24*%  #################
                # ################################################################

                match = self.fmt_re.search(gline)
                if match:
                    absolute = {'A': 'Absolute', 'I': 'Relative'}[match.group(2)]
                    if match.group(1) is not None:
                        self.gerber_zeros = cast(Literal["L", "T"], match.group(1))
                    self.int_digits = int(match.group(3))
                    self.frac_digits = int(match.group(4))
                    self.app.log.debug("Gerber format found. (%s) " % str(gline))

                    self.app.log.debug(
                        "Gerber format found. Gerber zeros = %s (L-omit leading zeros, T-omit trailing zeros, "
                        "D-no zero suppression)" % self.gerber_zeros)
                    self.app.log.debug("Gerber format found. Coordinates type = %s (Absolute or Relative)" % absolute)
                    continue

                # ################################################################
                # ######################## Mode (IN/MM)    #######################
                # #####################    Example: %MOIN*%  #####################
                # ################################################################
                match = self.mode_re.search(gline)
                if match:
                    self.units = cast(Literal["IN", "MM"], match.group(1))
                    self.app.log.debug("Gerber units found = %s" % self.units)
                    # Changed for issue #80
                    # self.convert_units(match.group(1))
                    s_tol = float(self._app_option("gerber_simp_tolerance")) / 25.4 \
                        if self.units == 'IN' else s_tol

                    self.conversion_done = True
                    continue

                # ################################################################
                # Combined Number format and Mode --- Allegro does this ##########
                # ################################################################
                match = self.fmt_re_alt.search(gline)
                if match:
                    absolute = {'A': 'Absolute', 'I': 'Relative'}[match.group(2)]
                    if match.group(1) is not None:
                        self.gerber_zeros = cast(Literal["L", "T"], match.group(1))
                    self.int_digits = int(match.group(3))
                    self.frac_digits = int(match.group(4))
                    self.app.log.debug("Gerber format found. (%s) " % str(gline))
                    self.app.log.debug(
                        "Gerber format found. Gerber zeros = %s (L-omit leading zeros, T-omit trailing zeros, "
                        "D-no zero suppression)" % self.gerber_zeros)
                    self.app.log.debug("Gerber format found. Coordinates type = %s (Absolute or Relative)" % absolute)

                    self.units = cast(Literal["IN", "MM"], match.group(5))
                    s_tol = float(self._app_option("gerber_simp_tolerance")) / 25.4 \
                        if self.units == 'IN' else s_tol

                    self.app.log.debug("Gerber units found = %s" % self.units)
                    # Changed for issue #80
                    # self.convert_units(match.group(5))
                    self.conversion_done = True
                    continue

                # ################################################################
                # ####     Search for OrCAD way for having Number format  ########
                # ################################################################
                match = self.fmt_re_orcad.search(gline)
                if match:
                    if match.group(1) is not None:
                        if match.group(1) == 'G74':
                            quadrant_mode = 'SINGLE'
                        elif match.group(1) == 'G75':
                            quadrant_mode = 'MULTI'
                        absolute = {'A': 'Absolute', 'I': 'Relative'}[match.group(3)]
                        if match.group(2) is not None:
                            self.gerber_zeros = cast(Literal["L", "T"], match.group(2))

                        self.int_digits = int(match.group(4))
                        self.frac_digits = int(match.group(5))
                        self.app.log.debug("Gerber format found. (%s) " % str(gline))
                        self.app.log.debug(
                            "Gerber format found. Gerber zeros = %s (L-omit leading zeros, T-omit trailing zeros, "
                            "D-no zero suppression)" % self.gerber_zeros)
                        self.app.log.debug(
                            "Gerber format found. Coordinates type = %s (Absolute or Relative)" % absolute)

                        self.units = cast(Literal["IN", "MM"], match.group(1))
                        s_tol = float(self._app_option("gerber_simp_tolerance")) / 25.4 \
                            if self.units == 'IN' else s_tol

                        self.app.log.debug("Gerber units found = %s" % self.units)
                        # Changed for issue #80
                        # self.convert_units(match.group(5))
                        self.conversion_done = True
                        continue

                # ################################################################
                # ############     Units (G70/1) OBSOLETE   ######################
                # ################################################################
                match = self.units_re.search(gline)
                if match:
                    if match.group(1) == "0":
                        obs_gerber_units = "IN"
                    elif match.group(1) == "1":
                        obs_gerber_units = "MM"
                    self.units = obs_gerber_units
                    s_tol = float(self._app_option("gerber_simp_tolerance")) / 25.4 \
                        if self.units == 'IN' else s_tol

                    self.app.log.warning("Gerber obsolete units found = %s" % obs_gerber_units)
                    # Changed for issue #80
                    # self.convert_units({'0': 'IN', '1': 'MM'}[match.group(1)])
                    self.conversion_done = True
                    continue

                # ################################################################
                # #####   Absolute/relative coordinates G90/1 OBSOLETE ###########
                # ################################################################
                match = self.absrel_re.search(gline)
                if match:
                    absolute = {'0': "Absolute", '1': "Relative"}[match.group(1)]
                    self.app.log.warning(
                        "Gerber obsolete coordinates type found = %s (Absolute or Relative) " % absolute)
                    continue

                # ################################################################
                # ######################## Aperture Macros #######################
                # Having this at the beginning will slow things down
                # but macros can have complicated statements than could
                # be caught by other patterns.
                # ################################################################
                # ################################################################
                if current_macro is None:  # No macro started yet
                    match = self.am1_re.search(gline)
                    # Start macro if there is a match, else not an AM, carry on.
                    if match:
                        self.app.log.debug("Starting macro. Line %d: %s" % (line_num, gline))
                        current_macro = match.group(1)
                        self.aperture_macros[current_macro] = ApertureMacro(name=current_macro)
                        if match.group(2):  # Append
                            self.aperture_macros[current_macro].append(match.group(2))
                        if match.group(3):  # Finish macro
                            # self.aperture_macros[current_macro].parse_content()
                            current_macro = None
                            self.app.log.debug("Macro complete in 1 line.")
                        continue
                else:  # Continue macro
                    self.app.log.debug("Continuing macro. Line %d." % line_num)
                    match = self.am2_re.search(gline)
                    if match:  # Finish macro
                        self.app.log.debug("End of macro. Line %d." % line_num)
                        self.aperture_macros[current_macro].append(match.group(1))
                        # self.aperture_macros[current_macro].parse_content()
                        current_macro = None
                    else:  # Append
                        self.aperture_macros[current_macro].append(gline)
                    continue

                # ################################################################
                # ##############   Aperture definitions %ADD...  #################
                # ################################################################
                match = self.ad_re.search(gline)
                if match:
                    # log.info("Found aperture definition. Line %d: %s" % (line_num, gline))
                    self.aperture_parse(match.group(1), match.group(2), match.group(3))
                    continue

                # ################################################################
                # ################  Operation code alone #########################
                # ###########   Operation code alone, usually just D03 (Flash) ###
                # self.opcode_re = re.compile(r'^D0?([123])\*$')
                # ################################################################
                match = self.opcode_re.search(gline)
                if match and current_aperture != "failure":
                    current_operation_code = int(match.group(1))
                    current_d = current_operation_code

                    if current_operation_code == 3:

                        # --- Buffered ---
                        try:
                            # self.app.log.debug("Bare op-code %d." % current_operation_code)
                            self._add_flash_to_buffers(
                                current_x, current_y, current_aperture, 
                                poly_buffer, follow_buffer, self.steps_per_circle
                            )

                        except IndexError:
                            self.app.log.warning("Line %d: %s -> Nothing there to flash!" % (line_num, gline))

                    continue

                # ################################################################
                # ################  Tool/aperture change  ########################
                # ################  Example: D12*         ########################
                # self.tool_re = re.compile(r'^(?:G54)?D(\d\d+)\*$')
                # ################################################################
                match = self.tool_re.search(gline)
                if match:
                    current_aperture = int(match.group(1))

                    # self.app.log.debug("Line %d: Aperture change to (%s)" % (line_num, current_aperture))

                    # If the aperture value is zero then make it something quite small but with a non-zero value
                    # such that it can be processed by FlatCAM.
                    # But first test to see if the aperture type is "aperture macro". In that case
                    # we should not test for "size" key as it does not exist in this case.
                    if current_aperture in self.tools and self.tools[current_aperture]["type"] != "AM":
                        if self.tools[current_aperture]["size"] == 0:
                            self.tools[current_aperture]["size"] = 10 ** -self.decimals

                    # if the detected aperture is not detected already then it means that we have a
                    if current_aperture not in self.tools:
                        current_aperture = "failure"
                        self.defective_aperture_detected = True

                    # self.app.log.debug(self.tools[current_aperture])

                    # Take care of the current path with the previous tool
                    path_length = len(path)

                    if path_length > 1:
                        if self.tools[last_path_aperture]["type"] == 'R':
                            # do nothing because 'R' type moving aperture is none at once
                            pass
                        else:
                            geo_s, geo_f, geo_dict = self._add_path_geometry_to_buffers(
                                path, last_path_aperture, poly_buffer, follow_buffer, steps
                            )
                            path = [path[-1]]
                    continue

                # ################################################################
                # ################  G36* - Begin region   ########################
                # ################################################################
                if self.regionon_re.search(gline) and current_aperture != "failure":
                    path, last_path_aperture = self._handle_region_start(
                        path, last_path_aperture, poly_buffer, follow_buffer,
                        steps, line_num, current_operation_code
                    )
                    making_region = True
                    continue

                # ################################################################
                # ################  G37* - End region     ########################
                # ################################################################
                if self.regionoff_re.search(gline) and current_aperture != "failure":
                    path, making_region = self._handle_region_end(
                        path, current_aperture, current_x, current_y,
                        poly_buffer, follow_buffer, steps, line_num,
                        current_operation_code, geo_s, geo_f
                    )
                    continue

                # ################################################################
                # ################  G01/2/3* - Interpolation mode change #########
                # ####  Can occur along with coordinates and operation code but ##
                # ####  sometimes by itself (handled here).  #####################
                # ####  Example: G01*                        #####################
                # ################################################################
                match = self.interp_re.search(gline)
                if match:
                    current_interpolation_mode = int(match.group(1))
                    continue

                # ################################################################
                # ######### G01 - Linear interpolation plus flashes  #############
                # ######### Operation code (D0x) missing is deprecated   #########
                # REGEX: r'^(?:G0?(1))?(?:X(-?\d+))?(?:Y(-?\d+))?(?:D0([123]))?\*$'
                # ################################################################
                match = self.lin_re.search(gline)
                if match and current_aperture != "failure":
                    # Dxx alone?
                    # if match.group(1) is None and match.group(2) is None and match.group(3) is None:
                    #     try:
                    #         current_operation_code = int(match.group(4))
                    #     except Exception:
                    #         pass  # A line with just * will match too.
                    #     continue
                    # NOTE: Letting it continue allows it to react to the
                    #       operation code.

                    # Parse coordinates
                    if match.group(2) is not None:
                        linear_x = parse_gerber_number(match.group(2),
                                                       self.int_digits, self.frac_digits, self.gerber_zeros)
                        current_x = linear_x
                    else:
                        linear_x = current_x
                    if match.group(3) is not None:
                        linear_y = parse_gerber_number(match.group(3),
                                                       self.int_digits, self.frac_digits, self.gerber_zeros)
                        current_y = linear_y
                    else:
                        linear_y = current_y

                    # Parse operation code
                    if match.group(4) is not None:
                        current_operation_code = int(match.group(4))

                        # Pen down: add segment
                    if current_operation_code == 1:
                        # if linear_x or linear_y are None, ignore those
                        if current_x is not None and current_y is not None:
                            # only add the point if it's a new one otherwise skip it (harder to process)
                            if path[-1] != [current_x, current_y]:
                                path.append([current_x, current_y])
                            elif len(path) == 1:
                                # it's a flash that is done by moving with pen up D2 and then just a pen down D1
                                # Reset path starting point
                                path = [[current_x, current_y]]

                                # treat the case when there is a flash inside a Gerber Region when the current_aperture
                                # is None
                                if current_aperture is not None:
                                    # --- BUFFERED ---
                                    # Draw the flash using helper
                                    self._add_flash_to_buffers(
                                        current_x, current_y, current_aperture,
                                        poly_buffer, follow_buffer, self.steps_per_circle
                                    )

                            if making_region is False:
                                # if the aperture is rectangle then add a rectangular shape having as parameters the
                                # coordinates of the start and end point and also the width and height
                                # of the 'R' aperture
                                try:
                                    if self.tools[current_aperture]["type"] == 'R':
                                        width = self.tools[current_aperture]['width']
                                        height = self.tools[current_aperture]['height']
                                        minx = min(path[0][0], path[1][0]) - width / 2
                                        maxx = max(path[0][0], path[1][0]) + width / 2
                                        miny = min(path[0][1], path[1][1]) - height / 2
                                        maxy = max(path[0][1], path[1][1]) + height / 2
                                        self.app.log.debug("Coords: %s - %s - %s - %s" % (minx, miny, maxx, maxy))

                                        geo_dict = {}
                                        geo_f = Point([current_x, current_y])
                                        follow_buffer.append(geo_f)
                                        geo_dict['follow'] = geo_f

                                        geo_s = shply_box(minx, miny, maxx, maxy)
                                        poly_buffer.append(geo_s)

                                        if self.is_lpc is True:
                                            geo_dict['clear'] = geo_s
                                        else:
                                            geo_dict['solid'] = geo_s

                                        self.tools.setdefault(current_aperture, {}).setdefault('geometry', []).append(geo_dict)
                                except Exception:
                                    pass

                            if current_aperture != "failure":
                                last_path_aperture = current_aperture
                            # we do this for the case that a region is done without having defined any aperture
                            if last_path_aperture is None:
                                if 0 not in self.tools:
                                    self.tools[0] = {}
                                    self.tools[0]['type'] = 'REG'
                                    self.tools[0]['size'] = 0.0
                                    self.tools[0]['geometry'] = []
                                last_path_aperture = 0
                        else:
                            self.app.inform.emit('[WARNING] %s: %s' %
                                                 (_("Coordinates missing, line ignored"), str(gline)))
                            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                                 _("GERBER file might be CORRUPT. Check the file !!!"))

                    elif current_operation_code == 2:
                        try:
                            path_length = len(path)
                        except TypeError:
                            path_length = 1

                        if path_length > 1:
                            geo_s = None

                            geo_dict = {}
                            # --- BUFFERED ---
                            # this treats the case when we are storing geometry as paths only
                            if making_region:
                                # we do this for the case that a region is done without having defined any aperture
                                if last_path_aperture is None:
                                    if 0 not in self.tools:
                                        self.tools[0] = {}
                                        self.tools[0]['type'] = 'REG'
                                        self.tools[0]['size'] = 0.0
                                        self.tools[0]['geometry'] = []
                                    last_path_aperture = 0
                                geo_f = Polygon()
                            else:
                                geo_f = LineString(path)

                            try:
                                if self.tools[last_path_aperture]["type"] != 'R':
                                    if not geo_f.is_empty:
                                        follow_buffer.append(geo_f)
                                        geo_dict['follow'] = geo_f
                            except Exception as e:
                                self.app.log.error("camlib.Gerber.parse_lines() --> %s" % str(e))
                                if not geo_f.is_empty:
                                    follow_buffer.append(geo_f)
                                    geo_dict['follow'] = geo_f

                            # this treats the case when we are storing geometry as solids
                            if making_region:
                                # we do this for the case that a region is done without having defined any aperture
                                if last_path_aperture is None:
                                    if 0 not in self.tools:
                                        self.tools[0] = {}
                                        self.tools[0]['type'] = 'REG'
                                        self.tools[0]['size'] = 0.0
                                        self.tools[0]['geometry'] = []
                                    last_path_aperture = 0

                                try:
                                    geo_s = Polygon(path)
                                except ValueError:
                                    self.app.log.warning("Problem %s %s" % (gline, line_num))
                                    self.app.inform.emit('[ERROR] %s: %s' %
                                                         (_("Region does not have enough points. "
                                                            "File will be processed but there are parser errors. "
                                                            "Line number"), str(line_num)))
                            else:
                                if last_path_aperture is None:
                                    self.app.log.warning("No aperture defined for current path. (%d)" % line_num)
                                # TODO: this may (should) fail
                                width = self.tools[last_path_aperture]["size"]
                                geo_s = geo_f.buffer(width / 1.999, steps)

                            try:
                                if self.tools[last_path_aperture]["type"] != 'R':
                                    if not geo_s.is_empty:
                                        poly_buffer.append(geo_s)

                                        if self.is_lpc is True:
                                            geo_dict['clear'] = geo_s
                                        else:
                                            geo_dict['solid'] = geo_s
                            except Exception as e:
                                self.app.log.error("camlib.Gerber.parse_lines() --> %s" % str(e))
                                poly_buffer.append(geo_s)

                                if self.is_lpc is True:
                                    geo_dict['clear'] = geo_s
                                else:
                                    geo_dict['solid'] = geo_s

                            self.tools.setdefault(last_path_aperture, {}).setdefault('geometry', []).append(geo_dict)

                        # if linear_x or linear_y are None, ignore those
                        if linear_x is not None and linear_y is not None:
                            path = [[linear_x, linear_y]]  # Start new path
                        else:
                            self.app.inform.emit('[WARNING] %s: %s' %
                                                 (_("Coordinates missing, line ignored"), str(gline)))
                            self.app.inform.emit('[WARNING_NOTCL] %s' %
                                                 _("GERBER file might be CORRUPT. Check the file !!!"))

                    # Flash
                    # Not allowed in region mode.
                    elif current_operation_code == 3:

                        # Create path draw so far.
                        try:
                            path_length = len(path)
                        except TypeError:
                            path_length = 1

                        if path_length > 1:
                            # --- Buffered ----
                            geo_dict = {}

                            # this treats the case when we are storing geometry as paths
                            geo_f = LineString(path)
                            if not geo_f.is_empty:
                                try:
                                    if self.tools[last_path_aperture]["type"] != 'R':
                                        follow_buffer.append(geo_f)
                                        geo_dict['follow'] = geo_f
                                except Exception as e:
                                    self.app.log.error("camlib.Gerber.parse_lines() --> G01 match D03 --> %s" % str(e))
                                    follow_buffer.append(geo_f)
                                    geo_dict['follow'] = geo_f

                            # this treats the case when we are storing geometry as solids
                            width = self.tools[last_path_aperture]["size"]
                            geo_s = geo_f.buffer(width / 1.999, steps)
                            if not geo_s.is_empty:
                                try:
                                    if self.tools[last_path_aperture]["type"] != 'R':
                                        poly_buffer.append(geo_s)

                                        if self.is_lpc is True:
                                            geo_dict['clear'] = geo_s
                                        else:
                                            geo_dict['solid'] = geo_s
                                except Exception:
                                    poly_buffer.append(geo_s)

                                    if self.is_lpc is True:
                                        geo_dict['clear'] = geo_s
                                    else:
                                        geo_dict['solid'] = geo_s

                            self.tools.setdefault(last_path_aperture, {}).setdefault('geometry', []).append(geo_dict)

                        # Reset path starting point
                        path = [[linear_x, linear_y]]

                        # --- BUFFERED ---
                        # Draw the flash
                        # this treats the case when we are storing geometry as paths
                        # Create flash geometry using helper
                        self._add_flash_to_buffers(
                            linear_x, linear_y, current_aperture,
                            poly_buffer, follow_buffer, self.steps_per_circle
                        )

                    # maybe those lines are not exactly needed,
                    # but it is easier to read the program as those coordinates
                    # are used in case that circular interpolation is encountered within the Gerber file
                    current_x = linear_x
                    current_y = linear_y

                    # self.app.log.debug("Line_number=%3s X=%s Y=%s (%s)" % (line_num, linear_x, linear_y, gline))
                    continue

                # ################################################################
                # ######### G74/75* - Single or multiple quadrant arcs  ##########
                # ################################################################
                match = self.quad_re.search(gline)
                if match:
                    if match.group(1) == '4':
                        quadrant_mode = 'SINGLE'
                    else:
                        quadrant_mode = 'MULTI'
                    continue

                # ################################################################
                # ######### G02/3 - Circular interpolation   #####################
                # ######### 2-clockwise, 3-counterclockwise  #####################
                # ######### Ex. format: G03 X0 Y50 I-50 J0 where the     #########
                # ######### X, Y coords are the coords of the End Point  #########
                # ################################################################
                match = self.circ_re.search(gline)
                if match and current_aperture != "failure":
                    arcdir = [None, None, "cw", "ccw"]

                    mode, circular_x, circular_y, i, j, d = match.groups()

                    try:
                        circular_x = parse_gerber_number(circular_x,
                                                         self.int_digits, self.frac_digits, self.gerber_zeros)
                    except Exception:
                        circular_x = current_x

                    try:
                        circular_y = parse_gerber_number(circular_y,
                                                         self.int_digits, self.frac_digits, self.gerber_zeros)
                    except Exception:
                        circular_y = current_y

                    # According to Gerber specification i and j are not modal, which means that when i or j are missing,
                    # they are to be interpreted as being zero
                    try:
                        i = parse_gerber_number(i, self.int_digits, self.frac_digits, self.gerber_zeros)
                    except Exception:
                        i = 0

                    try:
                        j = parse_gerber_number(j, self.int_digits, self.frac_digits, self.gerber_zeros)
                    except Exception:
                        j = 0

                    if quadrant_mode is None:
                        self.app.log.error(
                            "Found arc without preceding quadrant specification G74 or G75. (%d)" % line_num)
                        self.app.log.error(gline)
                        continue

                    if mode is None and current_interpolation_mode not in [2, 3]:
                        self.app.log.error("Found arc without circular interpolation mode defined. (%d)" % line_num)
                        self.app.log.error(gline)
                        continue
                    elif mode is not None:
                        current_interpolation_mode = int(mode)

                    # Set operation code if provided
                    if d is not None:
                        current_operation_code = int(d)

                    # Nothing created! Pen Up.
                    if current_operation_code == 2:
                        self.app.log.warning("Arc with D2. (%d)" % line_num)
                        try:
                            path_length = len(path)
                        except TypeError:
                            path_length = 1

                        if path_length > 1:
                            geo_dict = {}

                            if last_path_aperture is None:
                                self.app.log.warning("No aperture defined for current path. (%d)" % line_num)

                            # --- BUFFERED ---
                            width = self.tools[last_path_aperture]["size"]

                            # this treats the case when we are storing geometry as paths
                            geo_f = LineString(path)
                            if not geo_f.is_empty:
                                follow_buffer.append(geo_f)
                                geo_dict['follow'] = geo_f

                            # this treats the case when we are storing geometry as solids
                            buffered = geo_f.buffer(width / 1.999, steps)
                            if not buffered.is_empty:
                                poly_buffer.append(buffered)

                                if self.is_lpc is True:
                                    geo_dict['clear'] = buffered
                                else:
                                    geo_dict['solid'] = buffered

                            self.tools.setdefault(last_path_aperture, {}).setdefault('geometry', []).append(geo_dict)

                        current_x = circular_x
                        current_y = circular_y
                        path = [[current_x, current_y]]  # Start new path
                        continue

                    # Flash should not happen here
                    if current_operation_code == 3:
                        self.app.log.error("Trying to flash within arc. (%d)" % line_num)
                        continue

                    if quadrant_mode == 'MULTI':
                        center = [i + current_x, j + current_y]
                        radius = np.sqrt(i ** 2 + j ** 2)
                        start = np.arctan2(-j, -i)  # Start angle
                        # Numerical errors might prevent start == stop therefore
                        # we check ahead of time. This should result in a
                        # 360 degree arc.
                        if current_x == circular_x and current_y == circular_y:
                            stop = start
                        else:
                            stop = np.arctan2(-center[1] + circular_y, -center[0] + circular_x)  # Stop angle

                        this_arc = arc(
                            center,
                            radius,
                            start,
                            stop,
                            arcdir[current_interpolation_mode],
                            self.steps_per_circle
                        )

                        # The last point in the computed arc can have
                        # numerical errors. The exact final point is the
                        # specified (x, y). Replace.
                        this_arc[-1] = (circular_x, circular_y)

                        # Last point in path is current point
                        # current_x = this_arc[-1][0]
                        # current_y = this_arc[-1][1]
                        current_x, current_y = circular_x, circular_y

                        # Append
                        path += this_arc
                        last_path_aperture = current_aperture

                        continue

                    if quadrant_mode == 'SINGLE':

                        center_candidates = [
                            [i + current_x, j + current_y],
                            [-i + current_x, j + current_y],
                            [i + current_x, -j + current_y],
                            [-i + current_x, -j + current_y]
                        ]

                        # Calculate radius once, outside the loop
                        radius = np.sqrt(i ** 2 + j ** 2)

                        valid = False
                        # self.app.log.debug("I: %f  J: %f" % (i, j))
                        for center in center_candidates:
                            # Make sure radius to start is the same as radius to end.
                            radius2 = np.sqrt((center[0] - circular_x) ** 2 + (center[1] - circular_y) ** 2)
                            if radius2 < radius * 0.95 or radius2 > radius * 1.05:
                                continue  # Not a valid center.

                            # Correct i and j and continue as with multi-quadrant.
                            i = center[0] - current_x
                            j = center[1] - current_y

                            start = np.arctan2(-j, -i)  # Start angle
                            stop = np.arctan2(-center[1] + circular_y, -center[0] + circular_x)  # Stop angle
                            angle = abs(arc_angle(start, stop, arcdir[current_interpolation_mode]))
                            # self.app.log.debug("ARC START: %f, %f  CENTER: %f, %f  STOP: %f, %f" %
                            #           (current_x, current_y, center[0], center[1], circular_x, circular_y))
                            # self.app.log.debug("START Ang: %f, STOP Ang: %f, DIR: %s, ABS: %.12f <= %.12f: %s" %
                            #           (start * 180 / np.pi, stop * 180 / np.pi, arcdir[current_interpolation_mode],
                            #            angle * 180 / np.pi, np.pi / 2 * 180 / np.pi, angle <= (np.pi + 1e-6) / 2))

                            if angle <= (np.pi + 1e-6) / 2:
                                # self.app.log.debug("########## ACCEPTING ARC ############")
                                this_arc = arc(center, radius, start, stop,
                                               arcdir[current_interpolation_mode],
                                               self.steps_per_circle)

                                # Replace with exact values
                                this_arc[-1] = (circular_x, circular_y)

                                # current_x = this_arc[-1][0]
                                # current_y = this_arc[-1][1]
                                current_x, current_y = circular_x, circular_y

                                path += this_arc
                                last_path_aperture = current_aperture
                                valid = True
                                break

                        if valid:
                            continue
                        else:
                            self.app.log.warning("Invalid arc in line %d." % line_num)

                # ################################################################
                # ######### EOF - END OF FILE ####################################
                # ################################################################
                match = self.eof_re.search(gline)
                if match:
                    continue

                # ################################################################
                # ######### Line did not match any pattern. Warn user.  ##########
                # ################################################################
                self.app.log.warning("Line ignored (%d): %s" % (line_num, gline))
                # provide the app with a way to process the GUI events when in a blocking loop
                QtWidgets.QApplication.processEvents()

            try:
                path_length = len(path)
            except TypeError:
                path_length = 1

            if path_length > 1:
                # In case that G01 (moving) aperture is rectangular, there is no need to still create
                # another geo since we already created a shapely box using the start and end coordinates found in
                # path variable. We do it only for other apertures than 'R' type
                if self.tools[last_path_aperture]["type"] == 'R':
                    pass
                else:
                    # EOF, create shapely LineString if something still in path
                    # ## --- Buffered ---
                    geo_s, geo_f, geo_dict = self._add_path_geometry_to_buffers(
                        path, last_path_aperture, poly_buffer, follow_buffer, steps
                    )

            # Build source_file from accumulated lines
            self.source_file = '\n'.join(source_lines) + '\n'

            # Compose the layers closed by the polarity changes. Without earlier layers nothing changes: the last layer
            # is applied directly to the (empty) solid_geometry below.
            if earlier_layers:
                self.solid_geometry = self._compose_layers(earlier_layers)

            # ##########################################################################################################
            #   Creating the FINAL GEOMETRY
            # ##########################################################################################################
            # --- Apply buffer ---
            # this treats the case when we are storing geometry as paths
            self.follow_geometry = flatten_shapely_geometry(follow_buffer)

            # this treats the case when we are storing geometry as solids
            try:
                buff_length = len(poly_buffer)
            except TypeError:
                buff_length = 1

            if isinstance(self.solid_geometry, MultiPolygon):
                sol_geo_length = len(self.solid_geometry.geoms)
            elif isinstance(self.solid_geometry, list):
                sol_geo_length = len(self.solid_geometry)
            else:
                sol_geo_length = 1

            try:
                assert isinstance(self.solid_geometry, BaseGeometry)
                if buff_length == 0 and sol_geo_length in [0, 1] and self.solid_geometry.area == 0:
                    self.app.log.error("Object is not Gerber file or empty. Aborting Object creation.")
                    return 'fail'
            except TypeError as e:
                self.app.log.error("Object is not Gerber file or empty. Aborting Object creation. %s" % str(e))
                return 'fail'

            self.app.log.warning("Joining %d polygons." % buff_length)
            self.app.inform.emit('%s: %d.' % (_("Gerber processing. Joining polygons"), buff_length))

            prepare(np.array(poly_buffer, dtype=object))

            if use_simplification:
                poly_buffer = list(simplify(np.array(poly_buffer, dtype=object), s_tol))

            if self.use_buffer_for_union:
                self.app.log.debug("Union by buffer...")

                if use_buffering == 'full':
                    fused = fuse_parts(poly_buffer) if len(poly_buffer) > 0 else None
                    if fused:
                        new_poly = MultiPolygon(fused)
                        self.app.log.debug("Union by Clipper2 done.")
                    else:
                        new_poly = MultiPolygon(poly_buffer)
                        new_poly = new_poly.buffer(0.00000001)
                        new_poly = new_poly.buffer(-0.00000001)
                        self.app.log.debug("Union by GEOS buffer done (Clipper2 not used).")
                    self.app.log.warning("Union(buffer) done.")
                else:
                    new_poly = MultiPolygon(poly_buffer)

            else:
                self.app.log.debug("Union by union()...")
                new_poly = unary_union(poly_buffer)
                new_poly = new_poly.buffer(0, steps)
                self.app.log.warning("Union done.")

            # #########################################################################################################
            prepare(new_poly)
            # #########################################################################################################

            if current_polarity == 'D':
                self.app.inform.emit('%s' % _("Gerber processing. Applying Gerber polarity."))
                if new_poly.is_valid:
                    self.solid_geometry = self.solid_geometry.union(new_poly)
                else:
                    # I do this so whenever the parsed geometry of the file is not valid (intersections) it is still
                    # loaded. Instead of applying a union I add to a list of polygons.
                    final_poly = []
                    try:
                        for poly in new_poly:
                            final_poly.append(poly)
                    except TypeError:
                        final_poly.append(new_poly)

                    try:
                        for poly in self.solid_geometry:
                            final_poly.append(poly)
                    except TypeError:
                        final_poly.append(self.solid_geometry)

                    self.solid_geometry = final_poly

                # FIX for issue #347 - Sprint Layout generate Gerber files when the copper pour is enabled
                # it use a filled bounding box polygon to which add clear polygons (negative) to isolate the copper
                # features
                if extra_buffering:
                    candidate_geo = []
                    geo_to_buff: Sequence[BaseGeometry] | BaseGeometry
                    if isinstance(self.solid_geometry, MultiPolygon):
                        geo_to_buff = self.solid_geometry.geoms
                    else:
                        geo_to_buff = self.solid_geometry
                    if isinstance(geo_to_buff, BaseGeometry):
                        candidate_geo.append(geo_to_buff.buffer(-0.0000001))
                    else:
                        for p in geo_to_buff:
                            candidate_geo.append(p.buffer(-0.0000001))
                    self.solid_geometry = candidate_geo

            else:
                self.solid_geometry = self.solid_geometry.difference(new_poly)

            # flatten the solid geometry
            self.solid_geometry = flatten_shapely_geometry(self.solid_geometry)

            # import time
            # start = time.time()
            # #########################################################################################################
            prepare(self.solid_geometry)
            # #########################################################################################################
            # print(f"Time elapsed: {time.time() - start}; Is prepared? {is_prepared(self.solid_geometry)}")

            if clean_apertures:
                # clean the Gerber file of apertures with no geometry
                for apid, apvalue in list(self.tools.items()):
                    if 'geometry' not in apvalue:
                        self.tools.pop(apid)

            # init this for the following operations
            self.conversion_done = False

            if self.defective_aperture_detected:
                return "defective"
        except Exception as err:
            ex_type, ex, tb = sys.exc_info()
            traceback.print_tb(tb)
            # print traceback.format_exc()

            self.app.log.error("Gerber PARSING FAILED. Line %d: %s" % (line_num, gline))
            self.app.log.error(str(traceback.format_exc()))

            loc = '%s #%d %s: %s\n' % (_("Gerber Line"), line_num, _("Gerber Line Content"), gline) + repr(err)
            self.app.inform.emit('[ERROR] %s\n%s:' % (_("Gerber Parser ERROR"), loc))
            return 'fail'

        if is_excellon_gx2 is True:
            return 'drill'

    def create_flash_geometry(
        self,
        location: Any,
        aperture: Dict[str, Any],
        steps_per_circle: Optional[int] = None
    ) -> Any:

        # self.app.log.debug('Flashing @%s, Aperture: %s' % (location, aperture))

        if isinstance(location, list):
            location = Point(location)

        if aperture['type'] == 'C':  # Circles
            return location.buffer(aperture['size'] / 2, int(steps_per_circle))

        if aperture['type'] == 'R':  # Rectangles
            loc = location.coords[0]
            width = aperture['width']
            height = aperture['height']
            minx = loc[0] - width / 2
            maxx = loc[0] + width / 2
            miny = loc[1] - height / 2
            maxy = loc[1] + height / 2
            return shply_box(minx, miny, maxx, maxy).buffer(0.0000001)

        if aperture['type'] == 'O':  # Obround
            loc = location.coords[0]
            width = aperture['width']
            height = aperture['height']
            if width > height:
                p1 = Point(loc[0] + 0.5 * (width - height), loc[1])
                p2 = Point(loc[0] - 0.5 * (width - height), loc[1])
                c1 = p1.buffer(height * 0.5, int(steps_per_circle))
                c2 = p2.buffer(height * 0.5, int(steps_per_circle))
            else:
                p1 = Point(loc[0], loc[1] + 0.5 * (height - width))
                p2 = Point(loc[0], loc[1] - 0.5 * (height - width))
                c1 = p1.buffer(width * 0.5, int(steps_per_circle))
                c2 = p2.buffer(width * 0.5, int(steps_per_circle))
            return unary_union([c1, c2]).convex_hull

        if aperture['type'] == 'P':  # Regular polygon
            loc = location.coords[0]
            diam = aperture['diam']
            n_vertices = aperture['nVertices']
            points = []
            for i in range(0, n_vertices):
                x = loc[0] + 0.5 * diam * (np.cos(2 * np.pi * i / n_vertices))
                y = loc[1] + 0.5 * diam * (np.sin(2 * np.pi * i / n_vertices))
                points.append((x, y))
            ply = Polygon(points)
            if 'rotation' in aperture:
                # rotate about the aperture center (the flash point), not the bounds center: for an odd number of
                # vertices the 2 points are different
                ply = affinity.rotate(ply, aperture['rotation'], origin=loc)
            return ply

        if aperture['type'] == 'AM':  # Aperture Macro
            loc = location.coords[0]
            flash_geo = aperture['macro'].make_geometry(aperture['modifiers'])
            if flash_geo.is_empty:
                self.app.log.warning("Empty geometry for Aperture Macro: %s" % str(aperture['macro'].name))
            return affinity.translate(flash_geo, xoff=loc[0], yoff=loc[1])

        self.app.log.warning("Unknown aperture type: %s" % aperture['type'])
        return None

    def _add_path_geometry_to_buffers(
        self,
        path: List[List[float]],
        aperture_id: Optional[int],
        poly_buffer: List[Any],
        follow_buffer: List[Any],
        steps: int,
        making_region: bool = False
    ) -> Tuple[Optional[Any], Optional[Any], Dict[str, Any]]:
        """
        Add path geometry to poly_buffer and follow_buffer with proper geo_dict tracking.
        
        This extracts the repeated pattern of converting a path (list of [x,y] points)
        into Shapely geometry and adding it to the appropriate buffers.
        
        :param path: List of [x, y] coordinate pairs
        :param aperture_id: ID of the aperture to use for buffering width
        :param poly_buffer: List to append solid geometry to
        :param follow_buffer: List to append line geometry to
        :param steps: Number of steps per circle for arc approximation
        :param making_region: If True, creates Polygon instead of buffered LineString
        :return: Tuple of (geo_s, geo_f, geo_dict) or (None, None, {}) if path is empty
        """
        if len(path) < 2:
            return None, None, {}
        
        geo_dict = {}
        
        # Create follow geometry (line or polygon boundary)
        if making_region:
            geo_f = Polygon(path)
        else:
            geo_f = LineString(path)
        
        # Add to follow buffer
        if not geo_f.is_empty:
            follow_buffer.append(geo_f)
            geo_dict['follow'] = geo_f
        
        # Create solid geometry (buffered for traces, plain for regions)
        if making_region:
            geo_s = geo_f
        else:
            try:
                width = self.tools[aperture_id]["size"]
                geo_s = geo_f.buffer(width / 1.999, steps)
            except (KeyError, TypeError):
                geo_s = None
        
        # Add to poly buffer
        if geo_s and not geo_s.is_empty:
            if not geo_s.is_valid:
                self.app.log.warning("Found invalid Gerber geometry. Attempting fix...")
                geo_s = geo_s.buffer(0.0000001, steps)
            
            if geo_s and not geo_s.is_empty:
                poly_buffer.append(geo_s)
                if self.is_lpc is True:
                    geo_dict['clear'] = geo_s
                else:
                    geo_dict['solid'] = geo_s
        
        # Store in aperture geometry tracking
        if aperture_id is not None and (geo_s or geo_f):
            self.tools.setdefault(aperture_id, {}).setdefault('geometry', []).append(geo_dict)
        
        return geo_s, geo_f, geo_dict

    def _add_flash_to_buffers(
        self,
        x: float,
        y: float,
        aperture_id: Optional[int],
        poly_buffer: List[Any],
        follow_buffer: List[Any],
        steps: int
    ) -> Dict[str, Any]:
        """
        Create a flash at the given position and add to buffers.
        
        :param x: X coordinate for flash
        :param y: Y coordinate for flash
        :param aperture_id: Aperture ID to use for flash geometry
        :param poly_buffer: List to append solid geometry to
        :param follow_buffer: List to append point geometry to
        :param steps: Number of steps per circle for arc approximation
        :return: geo_dict with flash geometry, or {} on error
        """
        geo_dict = {}
        location = Point([x, y])
        
        # Add point to follow buffer
        follow_buffer.append(location)
        geo_dict['follow'] = location
        
        # Create flash geometry
        try:
            flash = self.create_flash_geometry(location, self.tools[aperture_id], steps)
            
            if flash and not flash.is_empty:
                poly_buffer.append(flash)
                
                if self.is_lpc is True:
                    geo_dict['clear'] = flash
                else:
                    geo_dict['solid'] = flash
                
                self.tools.setdefault(aperture_id, {}).setdefault('geometry', []).append(geo_dict)
        except (KeyError, TypeError) as e:
            self.app.log.warning(f"Flash at ({x}, {y}) failed: {e}")
        
        return geo_dict

    def _handle_region_start(
        self,
        path: List[List[float]],
        last_path_aperture: Optional[int],
        poly_buffer: List[Any],
        follow_buffer: List[Any],
        steps: int,
        line_num: int,
        current_operation_code: Optional[int]
    ) -> Tuple[List[List[float]], Optional[int]]:
        """
        Handle G36* command (begin region mode).
        
        Finalizes any pending path geometry before entering region mode.
        
        :param path: Current path coordinates
        :param last_path_aperture: Last used aperture ID
        :param poly_buffer: Polygon buffer (modified in place)
        :param follow_buffer: Follow geometry buffer (modified in place)
        :param steps: Arc resolution
        :param line_num: Current line number for error reporting
        :param current_operation_code: Last operation code
        :return: Tuple of (updated_path, updated_last_path_aperture)
        """
        try:
            path_length = len(path)
        except TypeError:
            path_length = 1

        if path_length > 1:
            # Finalize pending path before region
            geo_dict = {}
            geo_f = LineString(path)
            if not geo_f.is_empty:
                follow_buffer.append(geo_f)
                geo_dict['follow'] = geo_f

            # --- Buffered ----
            # Ensure last_path_aperture is valid before accessing tools
            if last_path_aperture is None or last_path_aperture not in self.tools:
                self.app.inform.emit(
                    '[ERROR] %s' % _(
                        "Gerber implementation format not supported. "
                        "Undefined aperture at line: %s. "
                        "Report to developers." % str(line_num)))
                path = [path[-1]]
                return path, last_path_aperture
            
            width = self.tools[last_path_aperture]["size"]
            geo_s = geo_f.buffer(width / 1.999, steps)
            if not geo_s.is_valid:
                self.app.log.warning(
                    "Found invalid Gerber geometry at line: %s. Fixing..." % str(line_num))
                geo_s = geo_s.buffer(0.0000001, steps)

            if not geo_s.is_valid:
                self.app.log.warning(
                    "Failed to fix the invalid Geometry found at line: %s" % str(line_num))
            else:
                self.tools.setdefault(last_path_aperture, {}).setdefault('geometry', [])
                # Handle MultiPolygon by iterating over .geoms
                pols_to_iterate = geo_s.geoms if isinstance(geo_s, BaseMultipartGeometry) else [geo_s]
                for pol in pols_to_iterate:
                    if not pol.is_empty:
                        poly_buffer.append(pol)
                        if self.is_lpc is True:
                            geo_dict['clear'] = pol
                        else:
                            geo_dict['solid'] = pol

                    if not pol.is_empty:
                        self.tools[last_path_aperture]['geometry'].append(geo_dict)

            path = [path[-1]]

        # Enter region mode
        # flashes are not allowed inside regions
        if current_operation_code == 3:
            current_operation_code = 2

        return path, last_path_aperture

    def _handle_region_end(
        self,
        path: List[List[float]],
        current_aperture: Optional[int],
        current_x: float,
        current_y: float,
        poly_buffer: List[Any],
        follow_buffer: List[Any],
        steps: int,
        line_num: int,
        current_operation_code: Optional[int],
        geo_s: Any = None,
        geo_f: Any = None
    ) -> Tuple[List[List[float]], bool]:
        """
        Handle G37* command (end region mode).
        
        Creates region polygon from path and adds to buffers.
        
        :param path: Region path coordinates
        :param current_aperture: Current aperture ID (it may be None for regions)
        :param current_x: Current X coordinate
        :param current_y: Current Y coordinate
        :param poly_buffer: Polygon buffer (modified in place)
        :param follow_buffer: Follow geometry buffer (modified in place)
        :param steps: Arc resolution
        :param line_num: Current line number
        :param current_operation_code: Last operation code
        :param geo_s: Optional solid geometry from path (for D02 edge case)
        :param geo_f: Optional follow geometry from path (for D02 edge case)
        :return: Tuple of (new_path, success)
        """

        # Ensure aperture 0 exists for region storage
        if 0 not in self.tools:
            self.tools[0] = {
                'type': 'REG',
                'size': 0.0,
                'geometry': []
            }

        # Handle D02 before G37 edge case
        # if D02 happened before G37 we now have a path with 1 element only
        if current_operation_code == 2:
            try:
                path_length = len(path)
            except TypeError:
                path_length = 1

            if path_length == 1:
                # Geometry was prepared previously, add it to buffers
                geo_dict = {}
                if geo_f:
                    if not geo_f.is_empty:
                        follow_buffer.append(geo_f)
                        geo_dict['follow'] = geo_f
                if geo_s:
                    if not geo_s.is_empty:
                        if not geo_s.is_valid:
                            self.app.log.warning(
                                "Found invalid geometry at line: %s" % str(line_num))
                        poly_buffer.append(geo_s)
                        if self.is_lpc is True:
                            geo_dict['clear'] = geo_s
                        else:
                            geo_dict['solid'] = geo_s

                if geo_s or geo_f:
                    self.tools[0]['geometry'].append(geo_dict)

                path = [[current_x, current_y]]  # Start new path

        # Validate path has minimum points
        try:
            path_length = len(path)
        except TypeError:
            path_length = 1

        if path_length < 3:
            return [[current_x, current_y]], False  # Skip invalid region

        # Create region polygon
        geo_dict = {}
        # Aperture is not used for region creation - regions are fill areas
        region_geo = Polygon(path)

        region_s = region_geo

        if not region_s.is_empty:
            if not region_s.is_valid:
                self.app.log.warning(
                    "Found invalid Gerber geometry at line: %s. Fixing..." % str(line_num))
                region_s = region_s.buffer(0.0000001, steps)
                region_s = flatten_shapely_geometry(region_s)

                if not region_s:
                    self.app.log.warning(
                        "Failed to fix the invalid Geometry found at line: %s" % str(line_num))
                else:
                    # Handle MultiPolygon by iterating over .geoms
                    pols_to_iterate = region_s.geoms if isinstance(region_s, BaseMultipartGeometry) else region_s
                    for pol in pols_to_iterate:
                        pol_f = pol.exterior
                        if not pol_f.is_empty:
                            follow_buffer.append(pol_f)
                            geo_dict['follow'] = pol

                        poly_buffer.append(pol)

                        if self.is_lpc is True:
                            geo_dict['clear'] = pol
                        else:
                            geo_dict['solid'] = pol

                        if not pol.is_empty:
                            self.tools[0]['geometry'].append(geo_dict)
            else:
                region_f = region_s.exterior
                if not region_f.is_empty:
                    follow_buffer.append(region_f)
                    geo_dict['follow'] = region_f

                poly_buffer.append(region_s)

                if self.is_lpc is True:
                    geo_dict['clear'] = region_s
                else:
                    geo_dict['solid'] = region_s

                if not region_s.is_empty:
                    self.tools[0]['geometry'].append(geo_dict)

        return [[current_x, current_y]], False  # Region ended (always False since G37 ends region)

    def create_geometry(self):
        """
        Geometry from a Gerber file is made up entirely of polygons.
        Every stroke (linear or circular) has an aperture which gives
        it thickness. Additionally, aperture strokes have non-zero area,
        and regions naturally do as well.

        :rtype : None
        :return: None
        """
        pass
        # self.buffer_paths()
        #
        # self.fix_regions()
        #
        # self.do_flashes()
        #
        # self.solid_geometry = unary_union(self.buffered_paths +
        #                                      [poly['polygon'] for poly in self.regions] +
        #                                      self.flash_geometry)

    def get_bounding_box(self, margin=0.0, rounded=False):
        """
        Creates and returns a rectangular polygon bounding at a distance of
        margin from the object's ``solid_geometry``. If margin > 0, the polygon
        can optionally have rounded corners of radius equal to margin.

        :param margin: Distance to enlarge the rectangular bounding
         box in both positive and negative, x and y axes.
        :type margin: float
        :param rounded: Whether to have rounded corners.
        :type rounded: bool
        :return: The bounding box.
        :rtype: Shapely.Polygon
        """

        assert isinstance(self.solid_geometry, BaseGeometry)
        bbox = self.solid_geometry.envelope.buffer(margin)
        if not rounded:
            bbox = bbox.envelope
        return bbox

    def bounds(self, flatten=None):
        """
        Returns coordinates of rectangular bounds
        of Gerber geometry: (xmin, ymin, xmax, ymax).

        :param flatten:     Not used, it is here for compatibility with base class method
        :return:            None
        """

        self.app.log.debug("parseGerber.Gerber.bounds()")

        if self.solid_geometry is None:
            self.app.log.debug("solid_geometry is None")
            return 0, 0, 0, 0

        def bounds_rec(obj):
            if type(obj) is list and type(obj) is not MultiPolygon:
                minx = np.inf
                miny = np.inf
                maxx = -np.inf
                maxy = -np.inf

                for k in obj:
                    if type(k) is dict:
                        for key in k:
                            minx_, miny_, maxx_, maxy_ = bounds_rec(k[key])
                            minx = min(minx, minx_)
                            miny = min(miny, miny_)
                            maxx = max(maxx, maxx_)
                            maxy = max(maxy, maxy_)
                    else:
                        if not k.is_empty:
                            try:
                                minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                            except Exception as e:
                                self.app.log.error("camlib.Gerber.bounds() --> %s" % str(e))
                                return

                            minx = min(minx, minx_)
                            miny = min(miny, miny_)
                            maxx = max(maxx, maxx_)
                            maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            else:
                # it's a Shapely object, return its bounds
                return obj.bounds

        bounds_coords = bounds_rec(self.solid_geometry)
        return bounds_coords

    def convert_units(self, obj_units):
        """
        Converts the units of the object to ``units`` by scaling all
        the geometry appropriately. This call ``scale()``. Don't call
        it again in descendants.

        :param obj_units:   "IN" or "MM"
        :type obj_units:    str
        :return:            Scaling factor resulting from unit change.
        :rtype:             float
        """

        if obj_units.upper() == self.units.upper():
            self.app.log.debug("parseGerber.Gerber.convert_units() --> Factor: 1")
            return 1.0

        if obj_units.upper() == "MM":
            factor = 25.4
            self.app.log.debug("parseGerber.Gerber.convert_units() --> Factor: %s" % str(factor))
        elif obj_units.upper() == "IN":
            factor = 1 / 25.4
            self.app.log.debug("parseGerber.Gerber.convert_units() --> Factor: %s" % str(factor))
        else:
            self.app.log.error("Unsupported units: %s" % str(obj_units))
            self.app.log.debug("parseGerber.Gerber.convert_units() --> Factor: 1")
            return 1.0

        self.units = obj_units
        self.file_units_factor = factor
        self.scale(factor, factor)
        return factor

    def import_svg(self, filename, object_type='gerber', flip=True, units=None):
        """
        Imports shapes from an SVG file into the object's geometry.

        :param filename:        Path to the SVG file.
        :type filename:         str
        :param object_type:     parameter passed further along
        :param flip:            Flip vertically.
        :type flip:             bool
        :param units:           FlatCAM units
        :return: None
        """

        self.app.log.debug("appParsers.ParseGerber.Gerber.import_svg()")

        # Parse into list of shapely objects
        assert hasattr(ET, "parse")
        svg_tree = ET.parse(filename)
        svg_root = svg_tree.getroot()

        # Change origin to bottom left
        # h = float(svg_root.get('height'))
        # w = float(svg_root.get('width'))
        svg_parsed_dims = svgparselength(svg_root.get('height'))
        h = svg_parsed_dims[0]
        svg_units = svg_parsed_dims[1]

        svg_unit_to_mm = {
            'mm': 1.0,
            'cm': 10.0,
            'in': 25.4,
            'px': 25.4 / 96.0,
            'pt': 25.4 / 72.0,
        }

        if svg_units in ['em', 'ex', '%']:
            self.app.log.error("ParseGerber.import_svg(). SVG units not supported: %s" % svg_units)
            return 'fail'

        units = self.app.app_units if units is None else units
        res = self._app_option("gerber_circle_steps", 64)
        factor = svgparse_viewbox(svg_root)

        if svg_units in svg_unit_to_mm:
            unit_factor = svg_unit_to_mm[svg_units]
            factor *= unit_factor
            h *= unit_factor

        geos = getsvggeo(svg_root, 'gerber', units=units, res=res, factor=factor, app=self.app)

        self.app.log.debug("appParsers.ParseGerber.Gerber.import_svg(). Finished parsing the SVG geometry.")

        if flip:
            geos = [affinity.translate(affinity.scale(g, 1.0, -1.0, origin=(0, 0)), yoff=h) for g in geos]
            self.app.log.debug("appParsers.ParseGerber.Gerber.import_svg(). SVG geometry was flipped.")

        # Work with a local list first because it makes typing a lot easier
        solid_geometry: list[BaseGeometry] = []
        if isinstance(self.solid_geometry, BaseGeometry):
            solid_geometry.append(self.solid_geometry)
        elif isinstance(self.solid_geometry, list):
            solid_geometry += self.solid_geometry

        if type(geos) == list:
            # HACK for importing QRCODE exported by FlatCAM
            try:
                geos_length = len(geos)
            except TypeError:
                geos_length = 1

            if geos_length == 1:
                geo_qrcode = [Polygon(geos[0].exterior)]
                for i_el in geos[0].interiors:
                    geo_qrcode.append(Polygon(i_el).buffer(0, resolution=res))
                geos = [poly for poly in geo_qrcode]

            solid_geometry += geos
        elif isinstance(geos, BaseGeometry):
            solid_geometry.append(geos)

        # flatten the self.solid_geometry list for import_svg() to import SVG as Gerber
        solid_geometry = flatten_shapely_geometry(solid_geometry)
        if 0 not in self.tools:
            self.tools[0] = {
                'type':         'REG',
                'size':         0.0,
                'geometry':     []
            }

        for pol in solid_geometry:
            prepare(pol)
            new_el = {'solid': pol, 'follow': LineString(pol.exterior.coords)}
            self.tools[0]['geometry'].append(new_el)
        self.solid_geometry = solid_geometry

    def import_dxf_as_gerber(self, filename, units='MM', text_mode='stroke'):
        """
        Imports shapes from a DXF file into the Gerber object geometry.

        :param filename:    Path to the DXF file.
        :type filename:     str
        :param units:       Application units
        :param text_mode:   'stroke' for CNC paths, 'outline' for filled shapes, 'none' to skip
        :return: None
        """

        self.app.log.debug("Parsing DXF file geometry into a Gerber object geometry.")

        self.multigeo = True

        # Parse into list of shapely objects
        try:
            dxf = ezdxf.readfile(filename)
        except (KeyError, Exception) as e:
            self.app.log.warning("Standard DXF read failed (%s), trying recovery mode..." % str(e))
            dxf, auditor = recover.readfile(filename)
        geos = getdxfgeo(dxf, text_mode=text_mode)

        # trying to optimize the resulting geometry by merging contiguous lines
        geos = list(self.flatten_list(geos))
        geos_polys = []
        geos_lines = []
        for g in geos:
            if isinstance(g, (Polygon, MultiPolygon)):
                geos_polys.append(g)
            else:
                geos_lines.append(g)

        merged_lines = linemerge(geos_lines)
        geos = geos_polys

        try:
            w_geo = merged_lines.geoms if isinstance(merged_lines, MultiLineString) else merged_lines
            for ml in w_geo:
                geos.append(ml)
        except TypeError:
            geos.append(merged_lines)

        # Work with a local list first because it makes typing a lot easier
        solid_geometry: list[BaseGeometry] = []
        if isinstance(self.solid_geometry, BaseGeometry):
            solid_geometry.append(self.solid_geometry)
        elif isinstance(self.solid_geometry, list):
            solid_geometry += self.solid_geometry

        if type(geos) is list:
            solid_geometry += geos
        elif isinstance(geos, BaseGeometry):
            solid_geometry.append(geos)

        # flatten the solid_geometry list for import_dxf() to import DXF as Gerber
        flat_geo = list(self.flatten_list(solid_geometry))
        if flat_geo:
            solid_geometry = unary_union(flat_geo)
            prepare(solid_geometry)
            self.solid_geometry = solid_geometry
            self.follow_geometry = solid_geometry
        else:
            return "fail"

        # create the self.tools data structure
        if 0 not in self.tools:
            self.tools[0] = {
                'type': 'REG',
                'size': 0.0,
                'geometry': []
            }

        for pol in flat_geo:
            new_el = {'solid': pol, 'follow': pol}
            self.tools[0]['geometry'].append(new_el)

    def _leaf_progress(self, top):
        """
        Progress callback for map_geometry: counts the leaves against self.geo_len and shows the percentage.
        The caller sets self.geo_len, self.old_disp_number and self.el_count first.

        :param top: the percentage that the last leaf maps to (99 or 100, kept per transform)
        """
        def tick(_leaf):
            self.el_count += 1
            disp_number = int(np.interp(self.el_count, [0, self.geo_len], [0, top]))
            if self.old_disp_number < disp_number <= 100:
                self.app.proc_container.update_view_text(' %d%%' % disp_number)
                self.old_disp_number = disp_number
        return tick

    def _map_aperture_geometry(self, fn):
        """Replace the 'solid', 'follow' and 'clear' geometry of every aperture geometry element with fn(geometry)."""
        for apid in self.tools:
            if 'geometry' in self.tools[apid]:
                for geo_el in self.tools[apid]['geometry']:
                    for key in ('solid', 'follow', 'clear'):
                        if key in geo_el:
                            geo_el[key] = fn(geo_el[key])

    def scale(
        self,
        xfactor: float,
        yfactor: Optional[float] = None,
        point: Optional[Tuple[float, float]] = None
    ) -> Optional[str]:
        """
        Scales the objects' geometry on the XY plane by a given factor.
        These are:

        * ``buffered_paths``
        * ``flash_geometry``
        * ``solid_geometry``
        * ``regions``

        NOTE:
        Does not modify the data used to create these elements. If these
        are recreated, the scaling will be lost. This behavior was modified
        because of the complexity reached in this class.

        :param xfactor: Number by which to scale on X axis.
        :type xfactor: float
        :param yfactor: Number by which to scale on Y axis.
        :type yfactor: float
        :param point: reference point for scaling operation
        :rtype : None
        """
        self.app.log.debug("parseGerber.Gerber.scale()")

        try:
            xfactor = float(xfactor)
        except Exception:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("Scale factor has to be a number: integer or float."))
            return

        if yfactor is None:
            yfactor = xfactor
        else:
            try:
                yfactor = float(yfactor)
            except Exception:
                self.app.inform.emit('[ERROR_NOTCL] %s' %
                                     _("Scale factor has to be a number: integer or float."))
                return

        if xfactor == 0 and yfactor == 0:
            return

        if point is None:
            px = 0
            py = 0
        else:
            px, py = point

        # variables to display the percentage of work done
        self.geo_len = 0
        try:
            if isinstance(self.solid_geometry, MultiPolygon):
                self.geo_len = len(self.solid_geometry.geoms)
            else:
                try:
                    self.geo_len = len(self.solid_geometry)
                except TypeError:
                    self.geo_len = 1
        except TypeError:
            self.geo_len = 1

        self.old_disp_number = 0
        self.el_count = 0

        tick = self._leaf_progress(99)

        def scale_geom(obj):
            return map_geometry(obj, lambda leaf: affinity.scale(leaf, xfactor, yfactor, origin=(px, py)), tick)

        self.solid_geometry = scale_geom(self.solid_geometry)
        self.follow_geometry = scale_geom(self.follow_geometry)

        # we need to scale the geometry stored in the Gerber apertures, too
        try:
            for apid in self.tools:
                new_geometry = []
                if 'geometry' in self.tools[apid]:
                    for geo_el in self.tools[apid]['geometry']:
                        new_geo_el = {}
                        if 'solid' in geo_el:
                            new_geo_el['solid'] = scale_geom(geo_el['solid'])
                        if 'follow' in geo_el:
                            new_geo_el['follow'] = scale_geom(geo_el['follow'])
                        if 'clear' in geo_el:
                            new_geo_el['clear'] = scale_geom(geo_el['clear'])
                        new_geometry.append(new_geo_el)

                self.tools[apid]['geometry'] = deepcopy(new_geometry)

            update_scale_metadata(self, xfactor, yfactor)
        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.scale() Exception --> %s' % str(e))
            return 'fail'

        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

        # ## solid_geometry ???
        #  It's a cascaded union of objects.
        # self.solid_geometry = affinity.scale(self.solid_geometry, factor,
        #                                      factor, origin=(0, 0))

        # # Now buffered_paths, flash_geometry and solid_geometry
        # self.create_geometry()

    def offset(self, vect: Tuple[float, float]) -> Optional[str]:
        """
        Offsets the objects' geometry on the XY plane by a given vector.
        These are:

        * ``buffered_paths``
        * ``flash_geometry``
        * ``solid_geometry``
        * ``regions``

        NOTE:
        Does not modify the data used to create these elements. If these
        are recreated, the scaling will be lost. This behavior was modified
        because of the complexity reached in this class.

        :param vect: (x, y) offset vector.
        :type vect: tuple
        :return: None
        """
        self.app.log.debug("ParseGerber.Gerber.offset()")

        try:
            dx, dy = vect
        except TypeError:
            self.app.inform.emit('[ERROR_NOTCL] %s' %
                                 _("An (x,y) pair of values are needed. "
                                   "Probable you entered only one value in the Offset field."))
            return

        if dx == 0 and dy == 0:
            return

        # Use vectorized translation for better performance
        # ## Solid geometry
        self.solid_geometry = translate_geometry(self.solid_geometry, dx, dy)
        self.follow_geometry = translate_geometry(self.follow_geometry, dx, dy)

        # Progress tracking - count apertures with geometry
        aperture_count = 0
        for apid in self.tools:
            if 'geometry' in self.tools[apid]:
                aperture_count += len(self.tools[apid]['geometry'])
        
        old_disp_number = 0
        el_count = 0

        # we need to offset the geometry stored in the Gerber apertures, too
        try:
            for apid in self.tools:
                if 'geometry' in self.tools[apid]:
                    for geo_el in self.tools[apid]['geometry']:
                        if 'solid' in geo_el:
                            geo_el['solid'] = translate_geometry(geo_el['solid'], dx, dy)
                        if 'follow' in geo_el:
                            geo_el['follow'] = translate_geometry(geo_el['follow'], dx, dy)
                        if 'clear' in geo_el:
                            geo_el['clear'] = translate_geometry(geo_el['clear'], dx, dy)

                        # Update progress at aperture level
                        el_count += 1
                        disp_number = int(np.interp(el_count, [0, aperture_count], [0, 100]))
                        if old_disp_number < disp_number <= 100:
                            self.app.proc_container.update_view_text(' %d%%' % disp_number)
                            old_disp_number = disp_number

        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.offset() Exception --> %s' % str(e))
            return 'fail'

        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

    def mirror(self, axis: str, point: List[float]) -> Optional[str]:
        """
        Mirrors the object around a specified axis passing through
        the given point. What is affected:

        * ``buffered_paths``
        * ``flash_geometry``
        * ``solid_geometry``
        * ``regions``

        NOTE:
        Does not modify the data used to create these elements. If these
        are recreated, the scaling will be lost. This behavior was modified
        because of the complexity reached in this class.

        :param axis: "X" or "Y" indicates around which axis to mirror.
        :type axis: str
        :param point: [x, y] point belonging to the mirror axis.
        :type point: list
        :return: None
        """
        self.app.log.debug("parseGerber.Gerber.mirror()")

        px, py = point
        xscale, yscale = {"X": (1.0, -1.0), "Y": (-1.0, 1.0)}[axis]

        # variables to display the percentage of work done
        self.geo_len = 0
        if isinstance(self.solid_geometry, BaseGeometry):
            self.geo_len = 1
        else:
            self.geo_len = len(self.solid_geometry)

        self.old_disp_number = 0
        self.el_count = 0

        tick = self._leaf_progress(99)

        def mirror_geom(obj):
            return map_geometry(obj, lambda leaf: affinity.scale(leaf, xscale, yscale, origin=(px, py)), tick)

        self.solid_geometry = mirror_geom(self.solid_geometry)
        self.follow_geometry = mirror_geom(self.follow_geometry)

        # we need to mirror the geometry stored in the Gerber apertures, too
        try:
            self._map_aperture_geometry(mirror_geom)
            update_mirror_metadata(self, axis)
        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.mirror() Exception --> %s' % str(e))
            return 'fail'

        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

    def skew(self, angle_x: float, angle_y: float, point: List[float]) -> Optional[str]:
        """
        Shear/Skew the geometries of an object by angles along x and y dimensions.

        Parameters
        ----------
        angle_x, angle_y : float, float
            The shear angle(s) for the x and y axes respectively. These can be
            specified in either degrees (default) or radians by setting
            use_radians=True.

        See shapely manual for more information:
        http://toblerity.org/shapely/manual.html#affine-transformations
        :param angle_x: the angle on X axis for skewing
        :param angle_y: the angle on Y axis for skewing
        :param point: reference point for skewing operation
        :return None
        """
        self.app.log.debug("parseGerber.Gerber.skew()")

        px, py = point

        if angle_x == 0 and angle_y == 0:
            return

        # variables to display the percentage of work done
        self.geo_len = 0
        if isinstance(self.solid_geometry, BaseGeometry):
            self.geo_len = 1
        else:
            self.geo_len = len(self.solid_geometry)

        self.old_disp_number = 0
        self.el_count = 0

        tick = self._leaf_progress(100)

        def skew_geom(obj):
            return map_geometry(obj, lambda leaf: affinity.skew(leaf, angle_x, angle_y, origin=(px, py)), tick)

        self.solid_geometry = skew_geom(self.solid_geometry)
        self.follow_geometry = skew_geom(self.follow_geometry)

        # we need to skew the geometry stored in the Gerber apertures, too
        try:
            self._map_aperture_geometry(skew_geom)
            update_skew_metadata(self)
        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.skew() Exception --> %s' % str(e))
            return 'fail'

        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

    def rotate(self, angle: float, point: List[float]) -> Optional[str]:
        """
        Rotate an object by a given angle around given coords (point)
        :param angle:
        :param point:
        :return:
        """
        self.app.log.debug("parseGerber.Gerber.rotate()")

        px, py = point

        if angle == 0:
            return

        # variables to display the percentage of work done
        self.geo_len = 0
        try:
            self.geo_len = len(self.solid_geometry)
        except TypeError:
            self.geo_len = 1

        self.old_disp_number = 0
        self.el_count = 0

        tick = self._leaf_progress(100)

        def rotate_geom(obj):
            return map_geometry(obj, lambda leaf: affinity.rotate(leaf, angle, origin=(px, py)), tick)

        self.solid_geometry = rotate_geom(self.solid_geometry)
        self.follow_geometry = rotate_geom(self.follow_geometry)

        # we need to rotate the geometry stored in the Gerber apertures, too
        try:
            self._map_aperture_geometry(rotate_geom)
            update_rotate_metadata(self, angle)
        except Exception as e:
            self.app.log.error('ParseGerber.Gerber.rotate() Exception --> %s' % str(e))
            return 'fail'
        self.app.inform.emit('[success] %s' % _("Done."))
        self.app.proc_container.new_text = ''

    def buffer(
        self,
        distance: float,
        join: str = "mitre",
        factor: Optional[bool] = None,
        only_exterior: bool = False,
        muted: bool = False
    ) -> None | str:

        """

        :param distance:        If 'factor' is True then distance is the factor
        :param join:            The type of joining used by the Shapely buffer method. Can be: round, mitre and bevel
        :param factor:          truthy: multiplicative mode, distance is the multiplier; None or False: distance mode
        :param only_exterior:   Bool. If True, the LineStrings are buffered only on the outside
        :param muted:           Bool. If True, the buffer operation is muted
        :return:                None | str
        """
        self.app.log.debug("parseGerber.Gerber.buffer()")

        if distance == 0:
            return

        # variables to display the percentage of work done
        self.geo_len = 0
        try:
            if isinstance(self.solid_geometry, (MultiPolygon, MultiLineString)):
                self.geo_len = len(self.solid_geometry.geoms)
            if isinstance(self.solid_geometry, list):
                self.geo_len = len(self.solid_geometry)
        except (TypeError, ValueError, RuntimeError):
            self.geo_len = 1

        self.old_disp_number = 0
        self.el_count = 0

        if not factor:
            def buffer_geom(obj):
                new_obj = []
                try:
                    work_geo = obj.geoms if isinstance(obj, (MultiPolygon, MultiLineString)) else obj
                    for g in work_geo:
                        new_obj.append(buffer_geom(g))
                except TypeError:
                    try:
                        new_obj = obj.buffer(distance, resolution=self.steps_per_circle, join_style=join)   # noqa
                        if isinstance(obj, (LinearRing, LineString)) and only_exterior is True:
                            new_obj = new_obj.exterior
                    except AttributeError:
                        new_obj = obj

                return new_obj

            self.solid_geometry = flatten_shapely_geometry(buffer_geom(self.solid_geometry))

            # we need to buffer the geometry stored in the Gerber apertures, too
            try:
                for apid in self.tools:
                    new_geometry = []
                    if 'geometry' in self.tools[apid]:
                        for geo_el in self.tools[apid]['geometry']:
                            new_geo_el = {}
                            if 'solid' in geo_el:
                                new_geo_el['solid'] = buffer_geom(geo_el['solid'])
                            if 'follow' in geo_el:
                                new_geo_el['follow'] = geo_el['follow']
                            if 'clear' in geo_el:
                                new_geo_el['clear'] = buffer_geom(geo_el['clear'])
                            new_geometry.append(new_geo_el)

                    self.tools[apid]['geometry'] = deepcopy(new_geometry)

                update_buffer_metadata(self, distance, join)
            except Exception as e:
                self.app.log.error('ParseGerber.Gerber.buffer() Exception --> %s' % str(e))
                return 'fail'
        else:
            # Multiplicative buffer (Stage 3): see plan_multiplicative_buffer(). Nothing is assigned until every step
            # has succeeded, so a rejection or a failure leaves the object unchanged.
            try:
                if not (np.isfinite(distance) and distance > 0):
                    raise MultiplicativeBufferRejected(_("The factor must be a positive number."))
                new_tools, new_solid, new_flag = plan_multiplicative_buffer(self, float(distance))
            except MultiplicativeBufferRejected as e:
                self.app.log.debug('ParseGerber.Gerber.buffer() rejected --> %s' % str(e))
                self.app.inform.emit('[WARNING_NOTCL] %s: %s' % (_("Gerber buffer by a factor was not done"), str(e)))
                self.app.proc_container.new_text = ''
                return 'fail'
            except Exception as e:
                self.app.log.error('ParseGerber.Gerber.buffer() Exception --> %s' % str(e))
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Gerber buffer by a factor failed"), str(e)))
                self.app.proc_container.new_text = ''
                return 'fail'

            # commit: plain assignments, together
            for apid, new_tool in new_tools.items():
                self.tools[apid] = new_tool
            self.solid_geometry = new_solid
            self.export_as_regions = new_flag
        if muted is False:
            self.app.inform.emit('[success] %s' % _("Gerber Buffer done."))
        self.app.proc_container.new_text = ''


# ---------------------------------------------------------------------------------------------------------------
# Stage 0C: the aperture metadata after a transform.
#
# The native Gerber writer declares the apertures from the metadata (C: size; R, O: width, height) and writes the
# 'follow' geometry of the records. After a transform the metadata must still describe the geometry, or
# ``Gerber.export_as_regions`` is set and the export writes the composed ``solid_geometry`` as regions. The rules
# are in the Stage 0C table. The helpers below are called by Gerber.scale, rotate, mirror, skew and buffer once the
# geometry is done; they never change the geometry or the records. Offset changes no metadata.
#
# Aperture keys (the parser, ``Gerber.aperture_parse``):
#   C: size | R, O: width, height, size (= hypot(width, height), the width of a track) |
#   P: diam, nVertices, size (= diam), rotation (optional, degrees) | AM: macro, modifiers | REG: size, geometry
# ---------------------------------------------------------------------------------------------------------------

_QUARTER_TURN_TOLERANCE = 1e-9      # degrees
_RECTANGLE_FLASH_TOLERANCE = 1e-5   # the parser rounds the corners of a rectangle flash with a 1e-7 buffer
_RECTANGLE_AREA_TOLERANCE = 1e-4    # relative


def _is_rectangle_flash(tool: Dict[str, Any], record: Dict[str, Any]) -> bool:
    """
    Tell if the solid geometry of a record of an 'R' aperture is a flash of that aperture, that is, its bounds
    are as wide and as high as the aperture. The parser makes a box around the end points for a track drawn with
    an 'R' aperture; the native writer can not write such a record (it flashes the aperture at the end point).

    :param tool:    the 'R' aperture
    :param record:  one record of the aperture
    :return:        False only when the solid geometry has other bounds than the aperture
    """
    solid = record.get('solid')
    bounds = getattr(solid, 'bounds', None)
    if solid is None or not bounds or len(bounds) != 4 or getattr(solid, 'is_empty', True):
        return True
    try:
        width = abs(bounds[2] - bounds[0])
        height = abs(bounds[3] - bounds[1])
        return abs(width - tool['width']) <= _RECTANGLE_FLASH_TOLERANCE and \
            abs(height - tool['height']) <= _RECTANGLE_FLASH_TOLERANCE
    except (KeyError, TypeError):
        return True


_REQUIRED_KEYS = {'R': ('width', 'height'), 'O': ('width', 'height'), 'P': ('diam',)}


def _is_well_formed(tool: Dict[str, Any]) -> bool:
    """
    Tell if an aperture has the keys its type needs ('R', 'O': width and height; 'P': diam). An aperture without
    them can not be updated, the callers send it to the regions export.
    """
    return all(key in tool for key in _REQUIRED_KEYS.get(str(tool.get('type')), ()))


def _flag_regions(gerber: Any, tool: Dict[str, Any]) -> None:
    """
    Mark that the aperture metadata can no longer describe the geometry, but only if the aperture has at least
    1 record (a flash or a track). The flag never goes back to False inside a transform.

    :param gerber:  the Gerber object
    :param tool:    the aperture
    :return:        None
    """
    if tool.get('geometry'):
        gerber.export_as_regions = True


def _has_track_records(tool: Dict[str, Any]) -> bool:
    """
    Tell if any record of the aperture is a track (its 'follow' is a line, not a flash point).

    :param tool:    the aperture
    :return:        True if there is a track record
    """
    for record in tool.get('geometry', ()):
        if getattr(record.get('follow'), 'geom_type', None) in ('LineString', 'LinearRing', 'MultiLineString'):
            return True
    return False


def _records_fill_rectangle(tool: Dict[str, Any]) -> bool:
    """
    Tell if the solid geometry of every record of an 'R' aperture is as large as the rectangle given by the
    width and height of the aperture. The parser rounds the corners of a rectangle flash with a 1e-7 buffer, and a
    mitre buffer of such a flash rounds the corners again (the many short edges of the tiny arcs get mitre joins
    that follow a circle), so the buffered flash is not a rectangle of the buffered width and height.

    :param tool:    the 'R' aperture, with its new width and height
    :return:        True if the records are rectangles of the aperture size
    """
    expected = tool['width'] * tool['height']
    for record in tool.get('geometry', ()):
        solid = record.get('solid')
        if solid is None:
            continue
        area = getattr(solid, 'area', None)
        if area is None or abs(area - expected) > _RECTANGLE_AREA_TOLERANCE * expected:
            return False
    return True


def _set_rectangle(tool: Dict[str, Any], width: float, height: float) -> None:
    """
    Store the width and height of an 'R' or 'O' aperture. 'size' is kept as the parser makes it: the diagonal.
    """
    tool['width'] = float(width)
    tool['height'] = float(height)
    tool['size'] = float(np.hypot(width, height))


def _set_polygon_rotation(tool: Dict[str, Any], rotation: float) -> None:
    """
    Store the rotation of a 'P' aperture, in degrees, reduced to [0, 360). The parser omits the key for 0, so it
    is left out when the aperture had none and the new value is 0.
    """
    rotation = float(rotation) % 360.0
    if 'rotation' in tool or rotation != 0.0:
        tool['rotation'] = rotation


def _scale_size(tool: Dict[str, Any], factor: float) -> None:
    """Multiply the generic 'size' by a (non-negative) factor, as the code did before Stage 0C."""
    if tool.get('size') is not None:
        tool['size'] = float(tool['size'] * factor)


def update_scale_metadata(gerber: Any, xfactor: float, yfactor: float) -> None:
    """
    Update the aperture metadata after ``Gerber.scale``. Sizes are always taken with ``abs()``.

    * ``s > 0`` uniform: C size, R and O width and height, P diam are multiplied; AM: regions.
    * ``s < 0`` uniform (a 180 degrees rotation): the same with ``abs(s)``; P rotation + 180.
    * non-uniform (``abs(sx) != abs(sy)``): R width * abs(sx), height * abs(sy); C, O, P and AM: regions.
    * ``scale(-s, s)`` / ``scale(s, -s)``: like the mirror rows, then the uniform ``abs(s)``. A negative
      ``yfactor`` negates Y (mirror X), a negative ``xfactor`` negates X (mirror Y).
    * a zero factor (the geometry collapses): every aperture is regions.

    :param gerber:  the Gerber object; its apertures are updated, the flag is set where needed
    :param xfactor: the scale factor on X
    :param yfactor: the scale factor on Y
    :return:        None
    """
    ax = abs(xfactor)
    ay = abs(yfactor)
    nonzero = ax > 0 and ay > 0
    uniform = nonzero and bool(np.isclose(ax, ay, rtol=1e-12, atol=0.0))

    for tool in gerber.tools.values():
        kind = str(tool.get('type'))
        representable = True
        if not _is_well_formed(tool):
            _flag_regions(gerber, tool)
            continue

        if kind == 'C':
            representable = uniform
            _scale_size(tool, ax)
        elif kind == 'R':
            representable = nonzero
            if representable:
                _set_rectangle(tool, tool['width'] * ax, tool['height'] * ay)
            else:
                tool['width'] *= ax
                tool['height'] *= ax
                _scale_size(tool, ax)
        elif kind == 'O':
            representable = uniform
            tool['width'] *= ax
            tool['height'] *= ax
            _scale_size(tool, ax)
        elif kind == 'P':
            representable = uniform
            tool['diam'] *= ax
            _scale_size(tool, ax)
            if representable:
                rotation = tool.get('rotation', 0.0)
                if xfactor < 0 and yfactor < 0:
                    rotation += 180.0
                elif yfactor < 0:
                    rotation = -rotation            # mirror X
                elif xfactor < 0:
                    rotation = 180.0 - rotation     # mirror Y
                _set_polygon_rotation(tool, rotation)
        elif kind == 'AM':
            representable = False
        else:
            _scale_size(tool, ax)

        if not representable:
            _flag_regions(gerber, tool)


def update_rotate_metadata(gerber: Any, angle: float) -> None:
    """
    Update the aperture metadata after ``Gerber.rotate`` by ``angle`` degrees.

    * C: unchanged.
    * R and O: an odd quarter turn swaps width and height, an even quarter turn changes nothing, any other
      angle is regions. A quarter turn is a multiple of 90 degrees within 1e-9 degrees, after reducing the
      angle modulo 360.
    * P: rotation + angle. AM: regions.

    :param gerber:  the Gerber object
    :param angle:   the rotation angle, in degrees
    :return:        None
    """
    reduced = float(angle) % 360.0
    quarters = round(reduced / 90.0)
    is_quarter_turn = abs(reduced - quarters * 90.0) <= _QUARTER_TURN_TOLERANCE
    is_odd_turn = is_quarter_turn and quarters % 2 == 1

    for tool in gerber.tools.values():
        kind = str(tool.get('type'))
        if not _is_well_formed(tool):
            _flag_regions(gerber, tool)
        elif kind in ('R', 'O'):
            if not is_quarter_turn:
                _flag_regions(gerber, tool)
            elif is_odd_turn:
                tool['width'], tool['height'] = tool['height'], tool['width']
        elif kind == 'P':
            _set_polygon_rotation(tool, tool.get('rotation', 0.0) + angle)
        elif kind == 'AM':
            _flag_regions(gerber, tool)


def update_mirror_metadata(gerber: Any, axis: str) -> None:
    """
    Update the aperture metadata after ``Gerber.mirror``. Mirror X negates Y and mirror Y negates X.

    * C, R, O: unchanged.
    * P: mirror X: rotation = -rotation; mirror Y: rotation = 180 - rotation.
    * AM: regions.

    :param gerber:  the Gerber object
    :param axis:    "X" or "Y"
    :return:        None
    """
    for tool in gerber.tools.values():
        kind = str(tool.get('type'))
        if kind == 'P':
            rotation = tool.get('rotation', 0.0)
            _set_polygon_rotation(tool, -rotation if axis == 'X' else 180.0 - rotation)
        elif kind == 'AM':
            _flag_regions(gerber, tool)


def update_skew_metadata(gerber: Any) -> None:
    """
    Update the aperture metadata after ``Gerber.skew`` with a non-zero angle: no aperture type survives a skew,
    so every aperture with records is regions. The metadata values are left as they are.

    :param gerber:  the Gerber object
    :return:        None
    """
    for tool in gerber.tools.values():
        if str(tool.get('type')) in ('C', 'R', 'O', 'P', 'AM'):
            _flag_regions(gerber, tool)


def _is_mitre_join(join: Any) -> bool:
    """
    Tell if the join style given to ``Gerber.buffer`` is the mitre join. Callers pass the Shapely name
    ('mitre', the default of the method, 'round', 'bevel') or the Shapely code (1 round, 2 mitre, 3 bevel).
    The plugin Transform passes 1 or 2, the Tcl command 'buffer' passes 1 or the names 'square' and 'bevel'.
    """
    if isinstance(join, str):
        return join.strip().lower() == 'mitre'
    try:
        return int(join) == 2
    except (TypeError, ValueError):
        return False


def update_buffer_metadata(gerber: Any, distance: float, join: Any) -> None:
    """
    Update the aperture metadata after ``Gerber.buffer`` by a distance (not by a factor).

    * C: size + 2d.
    * R: mitre join: width, height + 2d, if the records are exact rectangles of that size (see
      ``_records_fill_rectangle``: a parsed flash has corners rounded by the parser, so a positive mitre buffer
      rounds them and the result is regions); any other join rounds the corners: regions.
    * O: width, height + 2d. The parser draws a track of an O aperture with 'size' (the diagonal) as its width,
      and hypot(w + 2d, h + 2d) is not 'size' + 2d, so an O aperture with a track is regions.
    * P and AM: regions.
    * A size that is not positive after the buffer (a negative distance) is regions.

    Where the result is regions, the metadata keeps the values the code produced before Stage 0C.

    :param gerber:      the Gerber object
    :param distance:    the buffer distance
    :param join:        the join style: Shapely name or code
    :return:            None
    """
    grow = distance * 2
    mitre = _is_mitre_join(join)

    for tool in gerber.tools.values():
        kind = str(tool.get('type'))
        representable = True
        if not _is_well_formed(tool):
            _flag_regions(gerber, tool)
            continue

        if kind == 'C':
            _grow_size(tool, grow)
            representable = tool.get('size') is None or tool['size'] > 0
        elif kind in ('R', 'O'):
            tool['width'] += grow
            tool['height'] += grow
            representable = tool['width'] > 0 and tool['height'] > 0
            if kind == 'R':
                representable = representable and mitre and _records_fill_rectangle(tool)
            else:
                representable = representable and not _has_track_records(tool)
            if representable:
                _set_rectangle(tool, tool['width'], tool['height'])
            else:
                _grow_size(tool, grow)
        elif kind == 'P':
            tool['diam'] += grow
            _grow_size(tool, grow)
            representable = False
        elif kind == 'AM':
            representable = False
        else:
            _grow_size(tool, grow)

        if not representable:
            _flag_regions(gerber, tool)


class MultiplicativeBufferRejected(Exception):
    """The multiplicative buffer can not be done on this object; the message is for the user."""


def _scale_metadata_by_factor(tool: Dict[str, Any], factor: float) -> bool:
    """
    Update the aperture metadata of a COPY of an aperture after every flash was scaled by ``factor`` about its own
    point and every C track was rebuilt with the width ``size * factor``.

    * C: size * f.
    * R, O: width, height * f; size = hypot(width, height), as the parser makes it.
    * P: diam * f and size (= diam); nVertices and rotation are kept.
    * AM: not representable (the macro modifiers are not scaled): returns False.
    * REG: unchanged.

    :param tool:    the copy of the aperture, changed in place
    :param factor:  the positive factor
    :return:        False if the metadata can no longer describe the records (the regions export is needed)
    """
    kind = str(tool.get('type'))
    if kind == 'REG':
        return True
    if kind == 'AM':
        return False
    if not _is_well_formed(tool):
        return False
    if kind in ('R', 'O'):
        _set_rectangle(tool, tool['width'] * factor, tool['height'] * factor)
    elif kind == 'P':
        tool['diam'] = float(tool['diam'] * factor)
        tool['size'] = tool['diam']
    else:
        _scale_size(tool, factor)
    return True


def plan_multiplicative_buffer(gerber: Any, factor: float) -> Tuple[Dict[Any, Dict[str, Any]], BaseGeometry, bool]:
    """
    Plan the multiplicative buffer of a Gerber by ``factor`` (> 0) without changing the object.

    Supported: the flashes of every aperture type (the solid of the record is scaled about its 'follow' point), the
    tracks drawn with a C aperture (rebuilt from 'follow' with the width ``size * factor``, as the parser draws
    them: ``follow.buffer(width / 1.999, steps_per_circle)``) and the regions (aperture 0 / 'REG', unchanged).

    Rejected (MultiplicativeBufferRejected): a record with a non-empty 'clear' geometry (the records do not hold the
    global order of the file, so the polarity can not be composed again), a track drawn with another aperture than
    C (it has no defined width), a record that is not a flash or a track the parser makes, and an object whose
    composed geometry is not empty while its records hold no solid.

    :param gerber:  the Gerber object, not changed
    :param factor:  the positive factor
    :return:        (new apertures {apid: new dict}, new composed solid_geometry, new export_as_regions)
    """
    from appParsers.feature_access import normalize_polygonal    # imported here, like Gerber.needs_region_export

    steps = int(gerber.steps_per_circle)

    # (a) scan: nothing is built before the whole object is known to be supported
    for apid, tool in gerber.tools.items():
        kind = str(tool.get('type'))
        is_region = apid == 0 or kind == 'REG'
        for record in tool.get('geometry', ()):
            clear = record.get('clear')
            if clear is not None and not clear.is_empty:
                raise MultiplicativeBufferRejected(
                    _("the object has clear (negative polarity) features, the polarity can not be composed again"))
            if is_region:
                continue
            solid = record.get('solid')
            follow = record.get('follow')
            if follow is None:
                if solid is not None and not solid.is_empty:
                    raise MultiplicativeBufferRejected(
                        _("a feature of the aperture %s has no position to scale about") % str(apid))
            elif isinstance(follow, Point):
                if kind == 'R' and not _is_rectangle_flash(tool, record):
                    raise MultiplicativeBufferRejected(
                        _("the aperture %s is rectangular and draws a track, which has no defined width") % str(apid))
            elif kind != 'C':
                raise MultiplicativeBufferRejected(
                    _("the aperture %s draws a track and is not circular, a track has no defined width")
                    % str(apid))
            elif solid is not None:
                expected = follow.buffer(float(tool['size']) / 1.999, steps)
                if solid.symmetric_difference(expected).area > 1e-6 + 1e-4 * expected.area:
                    raise MultiplicativeBufferRejected(
                        _("a track of the aperture %s was not drawn by the Gerber parser") % str(apid))

    # (b) build the new apertures, records and metadata in copies
    new_tools = {}
    flag = bool(getattr(gerber, 'export_as_regions', False))
    dark = []
    for apid, tool in gerber.tools.items():
        new_tool = dict(tool)
        is_region = apid == 0 or str(tool.get('type')) == 'REG'
        new_records = []
        has_records = bool(tool.get('geometry'))
        if not is_region:
            if not _scale_metadata_by_factor(new_tool, factor) and has_records:
                flag = True
        for record in tool.get('geometry', ()):
            new_record = dict(record)
            solid = record.get('solid')
            follow = record.get('follow')
            if not is_region and solid is not None and follow is not None:
                if isinstance(follow, Point):
                    new_record['solid'] = affinity.scale(solid, factor, factor, origin=follow)
                else:
                    new_record['solid'] = follow.buffer(float(tool['size']) * factor / 1.999, steps)
            new_records.append(new_record)
            if new_record.get('solid') is not None:
                dark.append(new_record['solid'])
        if 'geometry' in tool:
            new_tool['geometry'] = new_records
        new_tools[apid] = new_tool

    # (c) the candidate aggregate from the new dark footprints of ALL the records (the polarity is checked above)
    if not dark and any(not g.is_empty for g in flatten_shapely_geometry(gerber.solid_geometry or [])):
        raise MultiplicativeBufferRejected(_("the object has no aperture records to rebuild its geometry from"))
    candidate = normalize_polygonal(dark)

    return new_tools, candidate, flag


def _grow_size(tool: Dict[str, Any], grow: float) -> None:
    """Add the growth (2 * the buffer distance) to the generic 'size', as the code did before Stage 0C."""
    if tool.get('size') is not None:
        tool['size'] = float(tool['size'] + grow)


def parse_gerber_number(
    strnumber: str,
    int_digits: int,
    frac_digits: int,
    zeros: str
) -> Optional[float]:
    """
    Parse a single number of Gerber coordinates.

    :param strnumber:       String containing a number in decimal digits
                            from a coordinate data block, possibly with a leading sign.
    :type strnumber:        str
    :param int_digits:      Number of digits used for the integer
                            part of the number
    :type frac_digits:      int
    :param frac_digits:     Number of digits used for the fractional
                            part of the number
    :type frac_digits:      int
    :param zeros:           If 'L', leading zeros are removed and trailing zeros are kept. Same situation for 'D' when
                            no zero suppression is done. If 'T', is in reverse.
    :type zeros:            str
    :return:                The number in floating point.
    :rtype:                 float
    """

    ret_val = None

    if zeros == 'L' or zeros == 'D':
        ret_val = int(strnumber) * (10 ** (-frac_digits))

    if zeros == 'T':
        int_val = int(strnumber)
        if int_val >= 0:
            ret_val = (int_val * (10 ** ((int_digits + frac_digits) - len(strnumber)))) * (10 ** (-frac_digits))
        else:
            # negative number therefore we have a '-' char in front of the strnumber
            ret_val = (int_val * (10 ** ((int_digits + frac_digits + 1) - len(strnumber)))) * (10 ** (-frac_digits))

        # if strnumber[0] == '-':
        #     int_val = strnumber[:(int_digits+1)]
        #     frac_val = strnumber[(int_digits+1):]
        # else:
        #     int_val = strnumber[:int_digits]
        #     frac_val = strnumber[(int_digits):]
        # ret_val = '%s.%s' % (int_val, frac_val)
        # ret_val = float(ret_val)

    return ret_val
