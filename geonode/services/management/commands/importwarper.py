from django.core.management.base import BaseCommand
from optparse import make_option
from urlparse import urlparse
from geonode.layers.models import Layer
from geonode.services.models import Service,ServiceLayer
import json
from geonode.people.utils import get_valid_user
from geonode.geoserver.helpers import set_attributes
import sys
import uuid
import requests
import re
import logging
import dateutil.parser
logger = logging.getLogger(__name__)

from geonode.services.views import _verify_service_type, _process_wms_service, _get_valid_name

class Command(BaseCommand):

    help = 'Import a remote map service into GeoNode'
    option_list = BaseCommand.option_list + (

        make_option('-o', '--owner', dest="owner", default=None,
                    help="Name of the user account which should own the imported layers"),
        make_option('-c', '--start', dest="start", default=1,
                    help="Page from which to begin to process"),
        make_option('-e', '--pages', dest="pages", default=None,
                    help="End page to process"),
        make_option('-r', '--registerlayers', dest="registerlayers", default=False,
                    help="Register all layers found in the service"),
        make_option('-u', '--username', dest="username", default=None,
                    help="Username required to login to this service if any"),
        make_option('-p', '--password', dest="password", default=None,
                    help="Username required to login to this service if any"),
        make_option('-s', '--security', dest="security", default=None,
                    help="Security permissions JSON - who can view/edit"),
    )

    args = 'url name type method'
    
    def handle(self, url, console=sys.stdout, **options):
        user = options.get('user')
        owner = get_valid_user(user)
        register_layers = options.get('registerlayers')
        username = options.get('username')
        password = options.get('password')
        pages = options.get('pages')
        start_page = options.get('start')
        perm_spec = options.get('permspec')

        register_service = True

        # First Check if this service already exists based on the URL
        base_url = url + 'maps'
        url = urlparse(url)
        domain = url.netloc
        payload = {'field':'title','query':'','show_warped': '1', 'format':'json'}
        headers = {'Content-Type': 'application/json','Accept': 'application/json'}
        request = requests.get(base_url,headers=headers,params=payload)
        records = json.loads(request.content)
        total_pages = int(records['total_pages'])
        current_page = 1
        if start_page is not None:
            start_page = int(start_page)
        if pages is not None:
            total_pages = int(pages)
        per_page = records['per_page']
        # We will need to introduce a loop later on here, for now let us just get the first item
        layer = records['items'][0]
        try:
            nyplservice = Service.objects.get(name=domain,parent=None)
        except:
            nyplservice = Service.objects.create(base_url=base_url+"/tile/"+str(layer['id'])+"/{z}/{x}/{y}.png",
                                     type='XYZ',
                                     method='X',
                                     name=domain,
                                     abstract= "Not provided",
                                     title= layer['title'],
                                     online_resource= base_url+"/tile/"+str(layer['id'])+"/{z}/{x}/{y}.png",
                                     owner=owner,
                                     parent=None)
            nyplservice.save()
            nyplservice.set_default_permissions()
        while start_page <= total_pages:
            payload = {'field':'title','query':'','show_warped': '1', 'format':'json', 'page': start_page}
            headers = {'Content-Type': 'application/json','Accept': 'application/json'}
            request = requests.get(base_url,headers=headers,params=payload)
            records = json.loads(request.content)
            current_page = records['current_page']
            logger.info("The page is" + str(current_page))
            logger.info("Fetched" + request.url)
            layers = records['items']
            for layer in layers:
                #generate the wms url
                owsurl = base_url + "/wms/" +str(layer['id'])
                type, server = _verify_service_type(owsurl)
                logger.debug("Map warper url being processed " +owsurl+ " of type " +type )
                if type in ['WMS', 'OWS']:
                    name = _get_valid_name(layer['title'])
                    name = domain + str(layer['id'])
                    if 'nypl_digital_id' in layer:
                        abstract = layer['nypl_digital_id'] + "  " + layer['description']
                        abstract = abstract + "    " + base_url + "/" + str(layer['id'])
                    else:
                        abstract = layer['description']
                    extract = date_extract(layer)
                    if 'date_depicted' not in layer:
                        layer['date_depicted'] = None
                    if 'published_date' not in layer:
                        layer['published_date'] = None
                    if layer['depicts_year'] is not None and layer['depicts_year'] != "":
                        layerdate = str(layer['depicts_year'])+"/01/01"
                    elif layer['date_depicted'] != None and layer['date_depicted'] != "":
                        layerdate = layer['date_depicted']
                    elif layer['published_date'] != None and layer['published_date'] != "":
                        layerdate = layer['published_date']
                    else:
                        if extract != None:
                            layerdate = extract
                        else:
                            layerdate = layer['created_at'][:10]
                            #layerdate = layer['created_at'].split()[0] + " "+ layer['created_at'].split()[1]
                    layerdate = dateutil.parser.parse(layerdate)
                    service = _process_wms_service(owsurl,name,"WMS", user, password, owner=owner, parent=nyplservice)
                    servicejson = json.loads(service.content)
                    serviceobject = Service.objects.get(id=servicejson[0]['service_id'])
                    #servicelayers = Layer.objects.filter(service=serviceobject).update(date=layerdate,abstract=abstract)
                    servicelayers = Layer.objects.filter(service=serviceobject)
                    for servicelayer in servicelayers:
                        servicelayer.date = layerdate
                        servicelayer.temporal_extent_start = layerdate
                        servicelayer.abstract = abstract
                        servicelayer.save()
            start_page = start_page + 1
def date_extract(layer):
    year = re.search('\d{4}', layer['title'])
    if year is None:
        year = re.search('\d{4}', layer['description'])
    if year != None and year > 1000 and year < 2020:
        year = year.group(0)
    else:
        year = None
    return year