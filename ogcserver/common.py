"""Core OGCServer classes and functions."""

import re
import sys
import copy
from sys import exc_info
from StringIO import StringIO
from lxml import etree as ElementTree
from traceback import format_exception, format_exception_only

try:
    from mapnik2 import Map, Color, Box2d as Envelope, render, Image, Layer, Style, Projection as MapnikProjection, Coord, mapnik_version
except ImportError:
    from mapnik import Map, Color, Envelope, render, Image, Layer, Style, Projection as MapnikProjection, Coord, mapnik_version
    
try:
    from PIL.Image import new
    from PIL.ImageDraw import Draw
    HAS_PIL = True
except ImportError:
    sys.stderr.write('Warning: PIL.Image not found: image based error messages will not be supported\n')
    HAS_PIL = False

from ogcserver.exceptions import OGCException, ServerConfigurationError



# from elementtree import ElementTree
# ElementTree._namespace_map.update({'http://www.opengis.net/wms': 'wms',
#                                    'http://www.opengis.net/ogc': 'ogc',
#                                    'http://www.w3.org/1999/xlink': 'xlink',
#                                    'http://www.w3.org/2001/XMLSchema-instance': 'xsi'
#                                    })

# TODO - need support for jpeg quality, and proper conversion into PIL formats
PIL_TYPE_MAPPING = {'image/jpeg': 'jpeg', 'image/png': 'png', 'image/png8': 'png256'}

class ParameterDefinition:

    def __init__(self, mandatory, cast, default=None, allowedvalues=None, fallback=False):
        """ An OGC request parameter definition.  Used to describe a
            parameter's characteristics.

            @param mandatory: Is this parameter required by the request?
            @type mandatory: Boolean.

            @param default: Default value to use if one is not provided
                            and the parameter is optional.
            @type default: None or any valid value.

            @param allowedvalues: A list of allowed values for the parameter.
                                  If a value is provided that is not in this
                                  list, an error is raised.
            @type allowedvalues: A python tuple of values.

            @param fallback: Whether the value of the parameter should fall
                             back to the default should an illegal value be
                             provided.
            @type fallback: Boolean.

            @return: A L{ParameterDefinition} instance.
        """
        if mandatory not in [True, False]:
            raise ServerConfigurationError("Bad value for 'mandatory' parameter, must be True or False.")
        self.mandatory = mandatory
        if not callable(cast):
            raise ServerConfigurationError('Cast parameter definition must be callable.')
        self.cast = cast
        self.default = default
        if allowedvalues and type(allowedvalues) != type(()):
            raise ServerConfigurationError("Bad value for 'allowedvalues' parameter, must be a tuple.")
        self.allowedvalues = allowedvalues
        if fallback not in [True, False]:
            raise ServerConfigurationError("Bad value for 'fallback' parameter, must be True or False.")
        self.fallback = fallback

class BaseServiceHandler:

    CONF_CONTACT_PERSON_PRIMARY = [
        ['contactperson', 'ContactPerson', str],
        ['contactorganization', 'ContactOrganization', str]
    ]
    
    CONF_CONTACT_ADDRESS = [   
        ['addresstype', 'AddressType', str],
        ['address', 'Address', str],
        ['city', 'City', str],
        ['stateorprovince', 'StateOrProvince', str],
        ['postcode', 'PostCode', str],
        ['country', 'Country', str]
    ]
    
    CONF_CONTACT = [
        ['contactposition', 'ContactPosition', str],
        ['contactvoicetelephone', 'ContactVoiceTelephone', str],
        ['contactelectronicmailaddress', 'ContactElectronicMailAddress', str]
    ]

    def processParameters(self, requestname, params):
        finalparams = {}
        for paramname, paramdef in self.SERVICE_PARAMS[requestname].items():
            if paramname not in params.keys() and paramdef.mandatory:
                raise OGCException('Mandatory parameter "%s" missing from request.' % paramname)
            elif paramname in params.keys():
                try:
                    params[paramname] = paramdef.cast(params[paramname])
                except OGCException:
                    raise
                except:
                    raise OGCException('Invalid value "%s" for parameter "%s".' % (params[paramname], paramname))
                if paramdef.allowedvalues and params[paramname] not in paramdef.allowedvalues:
                    if not paramdef.fallback:
                        raise OGCException('Parameter "%s" has an illegal value.' % paramname)
                    else:
                        finalparams[paramname] = paramdef.default
                else:
                    finalparams[paramname] = params[paramname]
            elif not paramdef.mandatory and paramdef.default:
                finalparams[paramname] = paramdef.default
        return finalparams
    
    def processServiceCapabilities(self, capetree):
        if len(self.conf.items('service')) > 0:
            servicee = capetree.find('Service')
            if servicee == None:
                servicee = capetree.find('{http://www.opengis.net/wms}Service')
            for item in self.CONF_SERVICE:
                if self.conf.has_option_with_value('service', item[0]):
                    value = self.conf.get('service', item[0]).strip()
                    try:
                        item[2](value)
                    except:
                        raise ServerConfigurationError('Configuration parameter [%s]->%s has an invalid value: %s.' % ('service', item[0], value))
                    if item[0] == 'onlineresource':
                        element = ElementTree.Element('%s' % item[1])
                        servicee.append(element)
                        element.set('{http://www.w3.org/1999/xlink}href', value)
                        element.set('{http://www.w3.org/1999/xlink}type', 'simple')
                    elif item[0] == 'keywordlist':
                        element = ElementTree.Element('%s' % item[1])
                        servicee.append(element)
                        keywords = value.split(',')
                        keywords = map(str.strip, keywords)
                        for keyword in keywords:
                            kelement = ElementTree.Element('Keyword')
                            kelement.text = keyword
                            element.append(kelement)
                    else:
                        element = ElementTree.Element('%s' % item[1])
                        element.text = to_unicode(value)
                        servicee.append(element)
            if len(self.conf.items_with_value('contact')) > 0:
                element = ElementTree.Element('ContactInformation')
                servicee.append(element)
                for item in self.CONF_CONTACT:
                    if self.conf.has_option_with_value('contact', item[0]):
                        value = self.conf.get('contact', item[0]).strip()
                        try:
                            item[2](value)
                        except:
                            raise ServerConfigurationError('Configuration parameter [%s]->%s has an invalid value: %s.' % ('service', item[0], value))
                        celement = ElementTree.Element('%s' % item[1])
                        celement.text = value
                        element.append(celement)
                for item in self.CONF_CONTACT_PERSON_PRIMARY + self.CONF_CONTACT_ADDRESS:
                    if item in self.CONF_CONTACT_PERSON_PRIMARY:
                        tagname = 'ContactPersonPrimary'
                    else:
                        tagname = 'ContactAddress'
                    if self.conf.has_option_with_value('contact', item[0]):
                        if element.find(tagname) == None:
                            subelement = ElementTree.Element(tagname)
                            element.append(subelement)
                        value = self.conf.get('contact', item[0]).strip()
                        try:
                            item[2](value)
                        except:
                            raise ServerConfigurationError('Configuration parameter [%s]->%s has an invalid value: %s.' % ('service', item[0], value))
                        celement = ElementTree.Element('%s' % item[1])
                        celement.text = value
                        subelement.append(celement)

class Response:

    def __init__(self, content_type, content):
        self.content_type = content_type
        self.content = content

class Version:

    def __init__(self, version = "1.1.1"):
        version = version.split('.')
        if len(version) != 3:
            raise OGCException('Badly formatted version number.')
        try:
            version = map(int, version)
        except:
            raise OGCException('Badly formatted version number.')
        self.version = version

    def __repr__(self):
        return '%s.%s.%s' % (self.version[0], self.version[1], self.version[2])

    def __cmp__(self, other):
        if isinstance(other, str):
            other = Version(other)
        if self.version[0] < other.version[0]:
            return -1
        elif self.version[0] > other.version[0]:
            return 1
        else:
            if self.version[1] < other.version[1]:
                return -1
            elif self.version[1] > other.version[1]:
                return 1
            else:
                if self.version[2] < other.version[2]:
                    return -1
                elif self.version[2] > other.version[2]:
                    return 1
                else:
                    return 0

class ListFactory:

    def __init__(self, cast):
        self.cast = cast

    def __call__(self, string):
        seq = string.split(',')
        return map(self.cast, seq)

def ColorFactory(colorstring):
    if re.match('^0x[a-fA-F0-9]{6}$', colorstring):
        return Color(eval('0x' + colorstring[2:4]), eval('0x' + colorstring[4:6]), eval('0x' + colorstring[6:8]))
    else:
        try:
            return Color(colorstring)
        except:
            raise OGCException('Invalid color value. Must be of format "0xFFFFFF", or any format acceptable my mapnik.Color()')

class CRS:

    def __init__(self, namespace, code):
        self.namespace = namespace.lower()
        self.code = int(code)
        self.proj = None

    def __repr__(self):
        return '%s:%s' % (self.namespace, self.code)

    def __eq__(self, other):
        if str(other) == str(self):
            return True
        return False

    def inverse(self, x, y):
        if not self.proj:
            self.proj = Projection('+init=%s:%s' % (self.namespace, self.code))
        return self.proj.inverse(Coord(x, y))

    def forward(self, x, y):
        if not self.proj:
            self.proj = Projection('+init=%s:%s' % (self.namespace, self.code))        
        return self.proj.forward(Coord(x, y))

class CRSFactory:

    def __init__(self, allowednamespaces):
        self.allowednamespaces = allowednamespaces

    def __call__(self, crsstring):
        if not re.match('^[A-Z]{3,5}:\d+$', crsstring):
            raise OGCException('Invalid format for the CRS parameter: %s' % crsstring, 'InvalidCRS')
        crsparts = crsstring.split(':')
        if crsparts[0] in self.allowednamespaces:
            return CRS(crsparts[0], crsparts[1])
        else:
            raise OGCException('Invalid CRS Namespace: %s' % crsparts[0], 'InvalidCRS')

def copy_layer(obj):
    lyr = Layer(obj.name)
    if hasattr(lyr,'title'):
        lyr.title = obj.title
    if hasattr(lyr,'abstract'):    
        lyr.abstract = obj.abstract
    # only if mapnik version supports it
    # http://trac.mapnik.org/ticket/503
    if hasattr(lyr, 'tolerance'):
        lyr.tolerance = obj.tolerance
        lyr.toleranceunits = obj.toleranceunits
    lyr.srs = obj.srs
    lyr.minzoom = obj.minzoom
    lyr.maxzoom = obj.maxzoom
    lyr.active = obj.active
    lyr.queryable = obj.queryable    
    lyr.clear_label_cache = obj.clear_label_cache
    lyr.datasource = obj.datasource
    if hasattr(obj,'wmsdefaultstyle'):
        lyr.wmsdefaultstyle = obj.wmsdefaultstyle
    if hasattr(obj,'wmsextrastyles'):
        lyr.wmsextrastyles = obj.wmsextrastyles
    if hasattr(obj,'meta_style'):
        lyr.meta_style = obj.meta_style
    if hasattr(lyr, 'wms_srs'):
        lyr.wms_srs = obj.wms_srs
    return lyr
      
class WMSBaseServiceHandler(BaseServiceHandler):

    def GetMap(self, params):
        m = self._buildMap(params)
        im = Image(params['width'], params['height'])
        render(m, im)
        format = PIL_TYPE_MAPPING[params['format']]
        return Response(params['format'].replace('8',''), im.tostring(format))

    def GetFeatureInfo(self, params, querymethodname='query_point'):
        m = self._buildMap(params)
        if params['info_format'] == 'text/plain':
            writer = TextFeatureInfo()
        elif params['info_format'] == 'text/xml':
            writer = XMLFeatureInfo()
        if params['query_layers'] and params['query_layers'][0] == '__all__':
            for layerindex, layer in enumerate(m.layers):
                featureset = getattr(m, querymethodname)(layerindex, params['i'], params['j'])
                features = featureset.features
                if features:
                    writer.addlayer(layer.name)
                    for feat in features:
                        writer.addfeature()
                        if mapnik_version() >= 800:
                            for prop in feat:
                                writer.addattribute(prop[0], prop[1])                        
                        else:
                            for prop in feat.properties:
                                writer.addattribute(prop[0], prop[1])
        else:
            for layerindex, layername in enumerate(params['query_layers']):
                if layername in params['layers']:
                    # TODO - pretty sure this is bogus, we can't pull from m.layers by the layerindex of the
                    # 'query_layers' subset, need to pull from:
                    # self.mapfactory.layers[layername]
                    if m.layers[layerindex].queryable:
                        featureset = getattr(m, querymethodname)(layerindex, params['i'], params['j'])
                        features = featureset.features
                        if features:
                            writer.addlayer(m.layers[layerindex].name)
                            for feat in features:
                                writer.addfeature()
                                if mapnik_version() >= 800:
                                    for prop in feat:
                                        writer.addattribute(prop[0], prop[1])                        
                                else:
                                    for prop in feat.properties:
                                        writer.addattribute(prop[0], prop[1])
                    else:
                        raise OGCException('Requested query layer "%s" is not marked queryable.' % layername, 'LayerNotQueryable')
                else:
                    raise OGCException('Requested query layer "%s" not in the LAYERS parameter.' % layername)
        return Response(params['info_format'], str(writer))

    def _buildMap(self, params):
        if str(params['crs']) not in self.allowedepsgcodes:
            raise OGCException('Unsupported CRS "%s" requested.' % str(params['crs']).upper(), 'InvalidCRS')
        if params['bbox'][0] >= params['bbox'][2]:
            raise OGCException("BBOX values don't make sense.  minx is greater than maxx.")
        if params['bbox'][1] >= params['bbox'][3]:
            raise OGCException("BBOX values don't make sense.  miny is greater than maxy.")

        # relax this for now to allow for a set of specific layers (meta layers even)
        # to be used without known their styles or putting the right # of commas...

        #if params.has_key('styles') and len(params['styles']) != len(params['layers']):
        #    raise OGCException('STYLES length does not match LAYERS length.')
        m = Map(params['width'], params['height'], '+init=%s' % params['crs'])

        if params.has_key('transparent') and params['transparent'] in ('FALSE','False','false'):
            if params['bgcolor']:
                m.background = params['bgcolor']
        elif not params.has_key('transparent') and self.mapfactory.map_attributes.get('bgcolor'):
            m.background = self.mapfactory.map_attributes['bgcolor']
        else:
            m.background = Color(0, 0, 0, 0)

        if params.has_key('buffer_size'):
            if params['buffer_size']:
                m.buffer_size = params['buffer_size']
        else:
            buffer_ = self.mapfactory.map_attributes.get('buffer_size')
            if buffer_:
                m.buffer_size = self.mapfactory.map_attributes['buffer_size']

        # haiti spec tmp hack! show meta layers without having
        # to request huge string to avoid some client truncating it!
        if params['layers'] and params['layers'][0] in ('osm_haiti_overlay','osm_haiti_overlay_900913'):
            for layer_obj in self.mapfactory.ordered_layers:
                layer = copy_layer(layer_obj)
                if not hasattr(layer,'meta_style'):
                    pass
                else:
                    layer.styles.append(layer.meta_style)
                    m.append_style(layer.meta_style, self.mapfactory.meta_styles[layer.meta_style])
                    m.layers.append(layer)        
        # a non WMS spec way of requesting all layers
        # uses orderedlayers that preserves original ordering in XML mapfile
        elif params['layers'] and params['layers'][0] == '__all__':
            for layer_obj in self.mapfactory.ordered_layers:
                # if we don't copy the layer here we get
                # duplicate layers added to the map because the
                # layer is kept around and the styles "pile up"...
                layer = copy_layer(layer_obj)
                if hasattr(layer,'meta_style'):
                    continue
                reqstyle = layer.wmsdefaultstyle
                if reqstyle in self.mapfactory.aggregatestyles.keys():
                    for stylename in self.mapfactory.aggregatestyles[reqstyle]:
                        layer.styles.append(stylename)
                else:
                    layer.styles.append(reqstyle)
                for stylename in layer.styles:
                    if stylename in self.mapfactory.styles.keys():
                        m.append_style(stylename, self.mapfactory.styles[stylename])
                m.layers.append(layer)
        else:
            for layerindex, layername in enumerate(params['layers']):
                if layername in self.mapfactory.meta_layers:
                    layer = copy_layer(self.mapfactory.meta_layers[layername])
                    layer.styles.append(layername)
                    m.append_style(layername, self.mapfactory.meta_styles[layername])
                else:
                    try:
                        # uses unordered dict of layers
                        # order based on params['layers'] request which
                        # should be originally informed by order of GetCaps response
                        layer = copy_layer(self.mapfactory.layers[layername])
                    except KeyError:
                        raise OGCException('Layer "%s" not defined.' % layername, 'LayerNotDefined')
                    try:
                        reqstyle = params['styles'][layerindex]
                    except IndexError:
                        reqstyle = ''
                    if reqstyle and reqstyle not in layer.wmsextrastyles:
                        raise OGCException('Invalid style "%s" requested for layer "%s".' % (reqstyle, layername), 'StyleNotDefined')
                    if not reqstyle:
                        reqstyle = layer.wmsdefaultstyle
                    if reqstyle in self.mapfactory.aggregatestyles.keys():
                        for stylename in self.mapfactory.aggregatestyles[reqstyle]:
                            layer.styles.append(stylename)
                    else:
                        layer.styles.append(reqstyle)

                    for stylename in layer.styles:
                        if stylename in self.mapfactory.styles.keys():
                            m.append_style(stylename, self.mapfactory.styles[stylename])
                        else:
                            raise ServerConfigurationError('Layer "%s" refers to non-existent style "%s".' % (layername, stylename))
                
                m.layers.append(layer)
        m.zoom_to_box(Envelope(params['bbox'][0], params['bbox'][1], params['bbox'][2], params['bbox'][3]))
        return m

class BaseExceptionHandler:

    def __init__(self, debug,base=False,home_html=None):
        self.debug = debug
        self.base = base
        self.home_html = home_html

    def getresponse(self, params):
        code = ''
        message = '\n'
        if self.base and not params:
            if self.home_html:
                message = open(self.home_html,'r').read()
            else:
                message = '''
                <h2>Welcome to the Mapnik OGCServer.</h2>
                <h3>Ready to accept map requests...</h3>
                <h4><a href="http://bitbucket.org/springmeyer/ogcserver/">More info</a></h4>
                '''
            return self.htmlhandler('', message)
        excinfo = exc_info()
        if self.debug:
            messagelist = format_exception(excinfo[0], excinfo[1], excinfo[2])
        else:
            messagelist = format_exception_only(excinfo[0], excinfo[1])
        message += ''.join(messagelist)
        if isinstance(excinfo[1], OGCException) and len(excinfo[1].args) > 1:
            code = excinfo[1].args[1]
        exceptions = params.get('exceptions', None)
        if self.debug:
            return self.htmlhandler(code, message)
        if not exceptions or not self.handlers.has_key(exceptions):
            exceptions = self.defaulthandler
        return self.handlers[exceptions](self, code, message, params)

    def htmlhandler(self,code,message):
        if code:
           resp_text = '<h2>OGCServer Error:</h2><pre>%s</pre>\n<h3>Traceback:</h3><pre>%s</pre>\n' %  (message, code)
        else:
           resp_text = message
        return Response('text/html', resp_text)

    def xmlhandler(self, code, message, params):
        ogcexcetree = copy.deepcopy(self.xmltemplate)
        e = ogcexcetree.find(self.xpath)
        e.text = message
        if code:
            e.set('code', code)
        return Response(self.xmlmimetype, ElementTree.tostring(ogcexcetree,pretty_print=True))

    def inimagehandler(self, code, message, params):
        im = new('RGBA', (int(params['width']), int(params['height'])))
        im.putalpha(new('1', (int(params['width']), int(params['height']))))
        draw = Draw(im)
        for count, line in enumerate(message.strip().split('\n')):
            draw.text((12,15*(count+1)), line, fill='#000000')
        fh = StringIO()
        format = PIL_TYPE_MAPPING[params['format']].replace('256','')
        im.save(fh, format)
        fh.seek(0)
        return Response(params['format'].replace('8',''), fh.read())

    def blankhandler(self, code, message, params):
        bgcolor = params.get('bgcolor', '#FFFFFF')
        bgcolor = bgcolor.replace('0x', '#')
        transparent = params.get('transparent', 'FALSE')
        if transparent in ('TRUE','true','True'):
            im = new('RGBA', (int(params['width']), int(params['height'])))
            im.putalpha(new('1', (int(params['width']), int(params['height']))))
        else:
            im = new('RGBA', (int(params['width']), int(params['height'])), bgcolor)
        fh = StringIO()
        format = PIL_TYPE_MAPPING[params['format']].replace('256','')
        im.save(fh, format)
        fh.seek(0)
        return Response(params['format'].replace('8',''), fh.read())

class Projection(MapnikProjection):
    
    def epsgstring(self):
        return self.params().split('=')[1].upper()

class TextFeatureInfo:

    def __init__(self):
        self.buffer = ''

    def addlayer(self, name):
        self.buffer += '\n[%s]\n' % name

    def addfeature(self):
        pass#self.buffer += '\n'

    def addattribute(self, name, value):
        self.buffer += '%s=%s\n' % (name, str(value))

    def __str__(self):
        return self.buffer

class XMLFeatureInfo:

    basexml = """<?xml version="1.0"?>
    <resultset>
    </resultset>
    """

    def __init__(self):
        self.rootelement = ElementTree.fromstring(self.basexml)

    def addlayer(self, name):
        layer = ElementTree.Element('layer')
        layer.set('name', name)
        self.rootelement.append(layer)
        self.currentlayer = layer

    def addfeature(self):
        feature = ElementTree.Element('feature')
        self.currentlayer.append(feature)
        self.currentfeature = feature
    
    def addattribute(self, name, value):
        attribute = ElementTree.Element('attribute')
        attname = ElementTree.Element('name')
        attname.text = name
        attvalue = ElementTree.Element('value')
        attvalue.text = unicode(value)
        attribute.append(attname)
        attribute.append(attvalue)
        self.currentfeature.append(attribute)
    
    def __str__(self):
        return '<?xml version="1.0"?>\n' + ElementTree.tostring(self.rootelement)

def to_unicode(obj, encoding='utf-8'):
    if isinstance(obj, basestring):
        if not isinstance(obj, unicode):
            obj = unicode(obj, encoding)
    return obj
