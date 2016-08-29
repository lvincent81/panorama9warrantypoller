#!/usr/bin/env python
"""
SYNOPSIS

    panorama9 [-h,--help] [-v,--verbose] [--version] [--logfile] [-i] [-e,-export]

DESCRIPTION

    General purpose tool to import / export data from the Panorama9 platform.

    If a file is given using the -i,-csv argument the tool will attempt to do warranty lookups through Dell, Lenovo, and HP APIs (overriding any ship date or warranty end date in the file) before uploading through the Panorama9 API.

    Currently the only fields that can be imported into are:
       {
        "location": {
          "latitude": "40.7127",
          "longitude": "-74.0059",
          "city": "New York City",
          "country": "United States"
        },
        "alias": "NYC file server",
        "warranty_date": "2014-10-07T19:15:02Z", *note ISO 8601 format, the Z is manditory
        "shipping_date": null,
        "first_use_date": null,
        "barcode": "abc3123",
        "manuals": "http://intra.local/manuals/abc3123",
        "drivers": "http://intra.local/drivers/abc3123",
        "system_config": "http://intra.local/system_config/abc3123",
        "notes": "remember to start print service after boot"
      }

EXAMPLES

    python panorama9 -i <input file> --logfile <log file>

    python panorama9 -e <export file>

EXIT STATUS

    TODO: List exit codes

AUTHOR

    Luke Vincent <lvincent@ci.durham.nh.us>

LICENSE

    This script is in the public domain, free from copyrights or restrictions.

VERSION

    $Id$
"""
panorama_api_key = ### 
dell_api_key = #### Try '849e027f476027a394edd656eaef4842'


from sys import argv,exit
from getopt import getopt, GetoptError
from json import loads,dumps
from urllib2 import Request, urlopen
from csv import writer
from datetime import datetime
from bs4 import BeautifulSoup as bs
from mechanize import Browser

def dell_lookup(service_tag):
    """Polls the Dell API for warranty information"""
    values = None
    headers = {
        'APIKey': dell_api_key,
        'Accept': 'Application/json',
        'Method': 'GET' 
    }
    request = Request('https://api.dell.com/support/assetinfo/v4/getassetwarranty/%s' % service_tag, headers=headers)
    response_body = urlopen(request).read()
    decoded_response = loads(response_body)
    shipdate = warrantyenddate = None
    for warranty in decoded_response['AssetWarrantyResponse'][0]['AssetEntitlementData']:
        if warranty['ServiceLevelDescription'] == 'Next Business Day Onsite':
            cursorenddate = datetime.strptime(warranty['EndDate'],'%Y-%m-%dT%H:%M:%S')
            if cursorenddate and (not warrantyenddate or warrantyenddate < cursorenddate):
                warrantyenddate = cursorenddate
    shipdate = datetime.strptime(decoded_response['AssetWarrantyResponse'][0]['AssetHeaderData']['ShipDate'],'%Y-%m-%dT%H:%M:%S')
    if shipdate and warrantyenddate:
        values = """{
            "price_currency": "%s",
            "price": %s,
            "shipping_date": "%s",
            "warranty_date": "%s"
            }
            """ % ('USD', '1000.0', shipdate.isoformat() + 'Z', warrantyenddate.isoformat() + 'Z') #  + 'Z' to comply with the ISO 8601 standard... 
    return values
def lenovo_lookup(serial,model):
    values = None
    shipdate = warrantyenddate = None
    if serial != 'INVALID' and model != 'INVALID': # an edge case from Panorama9
        url = 'http://support.lenovo.com/us/en/warrantylookup'
        b = Browser()
        b.set_handle_robots(False)
        b.open(url)
        try:
           b.select_form(predicate=lambda frm: 'id' in frm.attrs and frm.attrs['id'] == 'serialNumberForm')
        except FormNotFoundError:
          print("ERROR: Form not Found")        
        serial_cntl = b.form.find_control("SerialCode")
        machtype_cntl = b.form.find_control("MachineType")
        serial_cntl.readonly = machtype_cntl.readonly = False
        serial_cntl.value = serial
        machtype_cntl.value = model
        response =  b.submit()
        soup = bs(response.read(),'html.parser')
        tgt_div = soup.find(id="warranty_result_div")
        if tgt_div:
            for div in tgt_div.select('.cell3'):
                field,value = tuple(div.get_text(strip=True).split(':'))
                if field == 'Start Date':
                    shipdate = datetime.strptime(value,'%Y-%m-%d')                    
                elif field == 'End Date':
                    warrantyenddate = datetime.strptime(value,'%Y-%m-%d')
        if shipdate and warrantyenddate:
            values = """{
                "price_currency":"%s",
                "price":"%s",
                "shipping_date":"%s",
                "warranty_date":"%s"
                }
                """ % ('USD', '1000.0', shipdate.isoformat() + 'Z', warrantyenddate.isoformat() + 'Z') #  + 'Z' to comply with the ISO 8601 standard...
    return values        
def pollp9(devicetype):
    headers = {
      'Authorization': 'OAuth ' + panorama_api_key,
      'Accept': 'application/vnd.panorama9.com-v1+json'
    }
    request = Request('https://dashboard.panorama9.com/api/devices/%s' % devicetype, headers=headers)
    return urlopen(request).read()  
def poll4warranties(response_json):
    """Takes JSON encoded response from Panorma9 API and polls Vendor sites for warranty/shipping details

    Gathers UID, serial, and model (in the case of Lenovo) and then polls the appropiate endpoints with helper functions.  Based thier responses a values dictionary is built and returned

    Args:
        response_json = A properly encoded JSON string suitiable for conversion usin the Python JSON module

    Returns:
        A dictionary object with the UID from the response_json data as the key and collected information as values in a dictionary suitable as a "data" keyword argument in the Requests modules get/post methods or an empty dictionary

    Raises:

    """
    decoded_response = loads(response_json)
    asset_warranty_dct = {}    
    for asset in decoded_response:
        if asset['manufacturer'] == 'Dell':            
            values = dell_lookup(asset['serial_number'])
            if values:            
                asset_warranty_dct[asset['device_id']] = values
            else:
                print asset['device_id'],asset['serial_number'],asset['model']
        elif asset['manufacturer'] == 'Lenovo':            
            values = lenovo_lookup(asset['serial_number'], asset['model'])
            if values:                
                asset_warranty_dct[asset['device_id']] = values
            else:
                print asset['device_id'],asset['serial_number'],asset['model'], asset['name']
    return asset_warranty_dct
def postp9(asset_dict):
    headers = {
    'Authorization': 'OAuth ' + panorama_api_key,
    'Content-Type': 'application/json'
    }
    for id in asset_dict:        
        request = Request('https://dashboard.panorama9.com/api/devices/%s' % id, data=asset_dict[id], headers=headers)
        request.get_method = lambda: 'PATCH'
        response_body = urlopen(request).read()        
def main(argv):
    try:
        opts, args = getopt(argv,'h v i: e:',['help', 'verbose', 'version', 'logfile=', 'export='])
    except GetoptError, e:
        print 'panorama9 [-h,--help] [-v,--verbose] [--version] [--logfile] [-i] [-e,-export]'
        exit(2)
    for opt, arg in opts:
        if opt == '-h':
            print 'panorama9 [-h,--help] [-v,--verbose] [--version] [--logfile] [-i] [-e,-export]'
            exit()
        elif opt == '-i':
            input_file = arg
            infile = open(input_file,'wb')
            exit()
        elif opt == '-e':
            export_file = arg
            exfile = open(export_file,'wb')
            fieldnames = ['Device UID', 'Name', 'Service Tag', 'Manufacturer', 'Warranty End Date', 'Shipped Date', 'Unit Cost']
            excelcsvwriter = writer(exfile, dialect='excel')
            excelcsvwriter.writerow(fieldnames)
            #row = [i['device_id'],i['name'],i['serial_number'], i['manufacturer'], warrantyenddate.strftime('%Y-%m-%d'), shipdate.strftime('%Y-%m-%d')]
            #excelcsvwriter.writerow(row)
            exit()
    postp9(poll4warranties(pollp9('computers')))    
if __name__ == "__main__":
    main(argv[1:])