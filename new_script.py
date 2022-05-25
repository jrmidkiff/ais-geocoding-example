import petl as etl
import geopetl
from config import aisCredentials, source_creds,geocode_srid
from passyunk.parser import PassyunkParser
import requests
import cx_Oracle
import datetime as dt

# request AIS for X and Y coordinates
def ais_request(address_string,srid):
    '''
    :param address_string:
    :param srid:
    :return: list containing X and Y coordinates
    '''
    ais_url = aisCredentials['url']
    params = {'gatekeeperKey': aisCredentials['gatekeeperKey']}
    request = "{ais_url}{geocode_field}".format(ais_url=ais_url, geocode_field=address_string)
    request = request+'?srid='+srid
    try:
        r = requests.get(request, params=params)
        print('ais request response')
        if r.status_code ==404 :
            print('404 error')
            raise
    except Exception as e:
        print("Failed AIS request")
        raise e
    # extract coordinates from json request response
    feats = r.json()['features'][0]
    geo = feats.get('geometry')
    coords = geo.get('coordinates')
    return coords


# request tomtom for X and Y coordinates
def tomtom_request(street_str,srid):
    '''
    :param street_str: string
    :param srid:
    :return: list containing X and Y coordinates
    '''
    s = street_str.split(' ')
    address = '+'.join(s)
    request_str = '''https://citygeo-geocoder-aws.phila.city/arcgis/rest/services/TomTom/US_StreetAddress/GeocodeServer/findAddressCandidates?Street={}
                &City=&State=&ZIP=&Single+Line+Input=&outFields=&maxLocations=&matchOutOfRange=true&langCode=&locationType=&sourceCountry=&category=
                &location=&distance=&searchExtent=&outSR={}&magicKey=&f=pjson'''.format(address,srid)
    # send request to tomtom
    try:
        r = requests.get(request_str)
    except Exception as e:
        print("Failed tomtom request")
        raise e
    # try to get a top address candidate if any
    try:
        top_candidate =  r.json().get('candidates')[0].get('location')
        top_candidate = [top_candidate.get('x') ,top_candidate.get('y')]
    except:
        print('failed to geocode ', street_str)
        return ['NA','NA']
    return top_candidate


# conect to source table
target_dsn = cx_Oracle.makedsn(source_creds.get('dsn').get('host'),
                               source_creds.get('dsn').get('port'),
                               service_name=source_creds.get('dsn').get('service_name'))
target_conn = cx_Oracle.connect(source_creds.get('username'), source_creds.get('password'), target_dsn)
target_cursor = target_conn.cursor()

# # address summary table fields
if geocode_srid == 2272:
    adrsum_fields = ['street_address', 'geocode_x', 'geocode_y']
else:
    adrsum_fields = ['street_address', 'geocode_lon', 'geocode_lat']

# extract data from source table
address_summary_rows = etl.fromoraclesde(target_conn, 'ADDRESS_SUMMARY', fields=adrsum_fields)
#address_summary_rows.tocsv('address_summary_{}'.format(geocode_srid))
#address_summary_rows = etl.fromcsv('address_summary_{}.csv'.format(geocode_srid))

parser = PassyunkParser()
#input address data from input csv
input_address = etl.fromcsv('ais_geocoding_example_input.csv')#[adrsum_fields]
# add standardized address column to input csv using passyunk parser
input_address = input_address.addfield('addr_std', lambda p: parser.parse(p.street_address)['components']['output_address'])

#join input data with source table data
joined_addresses_to_address_summary = etl.leftjoin(input_address, address_summary_rows, lkey='addr_std', rkey='street_address', presorted=False )
#joined_addresses_to_address_summary.tocsv('test_joined_addresses_unsorted_{}.csv'.format(geocode_srid))
#joined_addresses_to_address_summary = etl.fromcsv('test_joined_addresses_unsorted_{}.csv'.format(geocode_srid))

#joined_table header
header = list(etl.fieldnames(joined_addresses_to_address_summary))

newlist = []
for row in joined_addresses_to_address_summary[1:]:
    rowzip = dict(zip(header, row))  # dictionary from etl data
    #if there is a longitude coordinates from address summary continue
    if rowzip.get('geocode_lon'):
        newlist.append(rowzip)
        continue
    elif rowzip.get('city'):
        srid = rowzip.get('srid')
        if rowzip.get('city') == 'philadelphia':
            geocoded = ais_request(rowzip.get('street_address'),str(srid))
        else:  # city is not philly
            geocoded = tomtom_request(rowzip.get('street_address'),str(srid))
    else:  # if no city column
        # if philadelphia in address use ais else try tomtom
        if 'philadelphia' in rowzip.get('street_address'):
            geocoded = ais_request(rowzip.get('street_address'),str(srid))
        else:
            try:
                geocoded = ais_request(rowzip.get('street_address'),str(srid))
            except:
                geocoded = tomtom_request(rowzip.get('street_address'),str(srid))
                
    #insert coordinates in row
    if geocode_srid== 2272:
        rowzip['geocode_x'] = geocoded[0]
        rowzip['geocode_y'] = geocoded[1]
    elif geocode_srid== 4326:
        rowzip['geocode_lon'] = geocoded[0]
        rowzip['geocode_lat'] = geocoded[1]

    newlist.append(rowzip)

newframe = etl.fromdicts(newlist,header=header)
newframe.tocsv('geocoded_output_{}.csv'.format(geocode_srid))

