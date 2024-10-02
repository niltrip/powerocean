"""
Created on Tue Oct 1 20:30:06 2024

@author: jdammers

Check response dict from EcoFlow API with a reference dict for comparison.

Note:
- The reference dict must be loaded in json format
- Differences between the two dicts will be indicated by the following keys:
    'in_dict1', the parameter is no more available in the current response from EcoFlow
    'in_dict2', new parameter found in the current response from EcoFlow

Arguments:
      sn: your serial number of the EcoFlow inverter
      username: your username to login
      password: your password to login
      fn_json:  the reference dict in json format for comparison with the current response
      save_new: set to True for saving the current response dict
      save_diff: set to True for saving the differences between the two dicts in json format

Example: from command line
    python check_parameter.py --sn HABCDEFGFIJK0001 --username me@myemail.de --password MyPasswd
                               --fn_json mydict.json
"""

from datetime import datetime
import json
import argparse
from pprint import pprint
from custom_components.powerocean.ecoflow import Ecoflow

# =====================================
# Helper functions
# =====================================
def compare_dicts(dict1, dict2, path='', check_values=True):
    diffs = {}
    for key in dict1:
        if key not in dict2:
            diffs[path + key] = {'in_dict1': dict1[key]}
        else:
            if isinstance(dict1[key], dict) and isinstance(dict2[key], dict):
                sub_diffs = compare_dicts(dict1[key], dict2[key], path + key + '.', check_values)
                if sub_diffs:
                    diffs[path + key] = sub_diffs
            elif check_values and dict1[key] != dict2[key]:
                diffs[path + key] = {'in_dict1': dict1[key], 'in_dict2': dict2[key]}

    for key in dict2:
        if key not in dict1:
            diffs[path + key] = {'in_dict2': dict2[key]}

    return diffs


def count_keys_of_dict(my_dict):
    count = 0
    for key, value in my_dict.items():
        count += 1
        if isinstance(value, dict):
            count += count_keys_of_dict(value)
    return count


def fetch_data(ef):
    import requests
    headers = {"authorization": f"Bearer {ef.token}"}
    request = requests.get(ef.url_user_fetch, headers=headers, timeout=30)
    return ef.get_json_response(request)


# =====================================
# Main Execution
# =====================================
def main(sn, username, password, fn_json, save_dict=False, save_diff=False):
    time = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    ef = Ecoflow(sn, username, password)
    auth_check = ef.authorize()

    if auth_check:
        response = fetch_data(ef)
        nkeys_new = count_keys_of_dict(response)

        if save_dict:
            fnout = 'Response-EcoFlowAPI_' + time + '.json'
            with open(fnout, 'w') as json_file:
                json.dump(response, json_file)

        with open(fn_json) as f:
            response_old = json.load(f)
            nkeys_old = count_keys_of_dict(response_old)

        diff = compare_dicts(response_old, response, check_values=False)

        if len(diff) == 0:
            print(">>> No new parameter encountered!")
        else:
            pprint(diff)

        print()
        print('Reference: %s' % fn_json)
        print("Total number of keys found in reference:        ", nkeys_old)
        print("Total number of keys found in current response: ", nkeys_new)

        if save_diff:
            fnout_diff = 'Response-Difference_' + time + '.json'
            with open(fnout_diff, 'w') as json_file:
                json.dump(diff, json_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Fetch and compare EcoFlow data.')

    parser.add_argument('--sn', required=True, help='Serial number')
    parser.add_argument('--username', required=True, help='Username (email)')
    parser.add_argument('--password', required=True, help='Password')
    parser.add_argument('--fn_json', required=True, help='File name of the old JSON data')

    parser.add_argument('--save_new', action='store_true', help='Save new response data as reference')
    parser.add_argument('--save_diff', action='store_true', help='Save difference data')

    args = parser.parse_args()

    main(args.sn, args.username, args.password, args.fn_json,
         save_dict=args.save_new,
         save_diff=args.save_diff)
