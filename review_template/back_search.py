#! /usr/bin/env python
import csv
import logging
import os
from datetime import datetime

import git
import pandas as pd
import requests

from review_template import grobid_client
from review_template import repo_setup
from review_template import utils

MAIN_REFERENCES = repo_setup.paths['MAIN_REFERENCES']
SCREEN = repo_setup.paths['SCREEN']
SEARCH_DETAILS = repo_setup.paths['SEARCH_DETAILS']
BATCH_SIZE = repo_setup.config['BATCH_SIZE']

data_dir = ''


def process_backward_search(entry):

    if entry['pipeline_status'] in ['pre_screen_excluded', 'excluded', 'NA']:
        return entry

    bib_filename = data_dir + 'search/' + entry['ID'] + '_bw_search.bib'
    pdf_filename = data_dir + 'pdfs/' + entry['ID'] + '.pdf'

    filename = entry.get('file', 'NA').replace('.pdf:PDF', '.pdf')\
        .replace(':', '')
    pdf_path = os.path.join(os.getcwd(), filename)
    if not os.path.exists(pdf_path):
        logging.error(f'File does not exist ({entry["ID"]})')
        return entry

    search_details = pd.read_csv(SEARCH_DETAILS)

    if bib_filename in search_details['source_url']:
        return entry

    logging.info(f'Extract references for {entry["ID"]}')
    # alternative python-batch:
    # https://github.com/kermitt2/grobid_client_python
    grobid_client.check_grobid_availability()

    options = {'consolidateHeader': '0', 'consolidateCitations': '1'}
    r = requests.post(
        grobid_client.get_grobid_url() + '/api/processReferences',
        files=dict(input=open(pdf_filename, 'rb')),
        data=options,
        headers={'Accept': 'application/x-bibtex'}
    )

    bib_content = r.text.encode('utf-8')
    with open(bib_filename, 'wb') as f:
        f.write(bib_content)
        entry['bib_filename'] = bib_filename

    if len(search_details.index) == 0:
        iteration_number = 1
    else:
        iteration_number = str(int(search_details['iteration'].max()))

    new_record = pd.DataFrame(
        [[
            bib_filename,
            r.text.count('\n@'),
            iteration_number,
            datetime.today().strftime('%Y-%m-%d'),
            datetime.today().strftime('%Y-%m-%d'),
            bib_filename,
            '',
            'backward_search.py',
            '',
        ]],
        columns=[
            'filename',
            'number_records',
            'iteration',
            'date_start',
            'date_completion',
            'source_url',
            'search_parameters',
            'responsible',
            'comment',
        ],
    )
    search_details = pd.concat([search_details, new_record])
    search_details.to_csv(SEARCH_DETAILS, index=False, quoting=csv.QUOTE_ALL)

    return entry


def create_commit(repo, bibfilenames):

    if 0 == len(bibfilenames):
        return

    repo.index.add([SEARCH_DETAILS])
    for f in bibfilenames:
        repo.index.add([f])

    processing_report = ''
    if os.path.exists('report.log'):
        with open('report.log') as f:
            processing_report = f.readlines()
        processing_report = \
            f'\nProcessing (batch size: {BATCH_SIZE})\n\n' + \
            ''.join(processing_report)

    repo.index.commit(
        '⚙️ Backward search ' + utils.get_version_flag() +
        utils.get_commit_report(os.path.basename(__file__)) +
        processing_report,
        author=git.Actor('script:backward_search.py', ''),
        committer=git.Actor(repo_setup.config['GIT_ACTOR'],
                            repo_setup.config['EMAIL']),
    )
    with open('report.log', 'r+') as f:
        f.truncate(0)
    return


def main():
    repo = git.Repo()
    utils.require_clean_repo(repo)
    grobid_client.start_grobid()

    with open('report.log', 'r+') as f:
        f.truncate(0)
    logging.info('Backward search')

    bib_database = utils.load_references_bib(True, initialize=True)
    bib_database = utils.add_pipeline_status_info(bib_database)

    for entry in bib_database.entries:
        process_backward_search(entry)

    create_commit(repo, [x['bib_filename']
                  for x in bib_database.entries if 'bib_filename' in x])
    return


if __name__ == '__main__':
    main()
