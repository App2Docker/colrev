#! /usr/bin/env python
import logging
import os
import typing
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import zope.interface
from colrev.cli import console_duplicate_instance_label
from dacite import from_dict

from colrev_core.exceptions import DedupeError
from colrev_core.process import DedupeEndpoint
from colrev_core.process import DefaultSettings


class colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    ORANGE = "\033[93m"
    BLUE = "\033[94m"
    END = "\033[0m"


@zope.interface.implementer(DedupeEndpoint)
class SimpleDedupeEndpoint:
    """Simple duplicate identification when the sample size is too small"""

    def __init__(self, *, SETTINGS):
        self.SETTINGS = from_dict(data_class=DefaultSettings, data=SETTINGS)

    def __calculate_similarities_record(
        self, *, DEDUPE, references: pd.DataFrame
    ) -> list:
        from colrev_core.record import Record

        # Note: per definition, similarities are needed relative to the last row.
        references["similarity"] = 0
        references["details"] = 0
        sim_col = references.columns.get_loc("similarity")
        details_col = references.columns.get_loc("details")
        for base_record_i in range(0, references.shape[0]):
            sim_details = Record.get_similarity_detailed(
                df_a=references.iloc[base_record_i], df_b=references.iloc[-1]
            )
            DEDUPE.REVIEW_MANAGER.report_logger.debug(
                f"Similarity score: {sim_details['score']}"
            )
            DEDUPE.REVIEW_MANAGER.report_logger.debug(sim_details["details"])

            references.iloc[base_record_i, sim_col] = sim_details["score"]
            references.iloc[base_record_i, details_col] = sim_details["details"]
        # Note: return all other records (not the comparison record/first row)
        # and restrict it to the ID, similarity and details
        ck_col = references.columns.get_loc("ID")
        sim_col = references.columns.get_loc("similarity")
        details_col = references.columns.get_loc("details")
        return references.iloc[:, [ck_col, sim_col, details_col]]

    def append_merges(self, *, DEDUPE, batch_item: dict) -> dict:

        DEDUPE.REVIEW_MANAGER.logger.debug(f'append_merges {batch_item["record"]}')

        references = batch_item["queue"]

        # if the record is the first one added to the records
        # (in a preceding processing step), it can be propagated
        # if len(batch_item["queue"]) < 2:
        if len(references.index) < 2:
            return {
                "ID1": batch_item["record"],
                "ID2": "NA",
                "similarity": 1,
                "decision": "no_duplicate",
            }

        # df to get_similarities for each other record
        references = self.__calculate_similarities_record(
            DEDUPE=DEDUPE, references=references
        )
        # drop the first row (similarities are calculated relative to the last row)
        references = references.iloc[:-1, :]
        # if batch_item['record'] == 'AdamsNelsonTodd1992':
        #     references.to_csv('last_similarities.csv')

        max_similarity = references.similarity.max()

        # TODO: it may not be sufficient to consider
        # the record with the highest similarity

        ret = {}
        if max_similarity <= batch_item["MERGING_NON_DUP_THRESHOLD"]:
            # Note: if no other record has a similarity exceeding the threshold,
            # it is considered a non-duplicate (in relation to all other records)
            DEDUPE.REVIEW_MANAGER.logger.debug(f"max_similarity ({max_similarity})")
            ret = {
                "ID1": batch_item["record"],
                "ID2": "NA",
                "similarity": max_similarity,
                "decision": "no_duplicate",
            }

        elif (
            max_similarity > batch_item["MERGING_NON_DUP_THRESHOLD"]
            and max_similarity < batch_item["MERGING_DUP_THRESHOLD"]
        ):

            ID = references.loc[references["similarity"].idxmax()]["ID"]
            DEDUPE.REVIEW_MANAGER.logger.debug(
                f"max_similarity ({max_similarity}): {batch_item['record']} {ID}"
            )
            details = references.loc[references["similarity"].idxmax()]["details"]
            DEDUPE.REVIEW_MANAGER.logger.debug(details)
            # record_a, record_b = sorted([ID, record["ID"]])
            msg = (
                f'{batch_item["record"]} - {ID}'.ljust(35, " ")
                + f"  - potential duplicate (similarity: {max_similarity})"
            )
            DEDUPE.REVIEW_MANAGER.report_logger.info(msg)
            DEDUPE.REVIEW_MANAGER.logger.info(msg)
            ret = {
                "ID1": batch_item["record"],
                "ID2": ID,
                "similarity": max_similarity,
                "decision": "potential_duplicate",
            }

        else:  # max_similarity >= batch_item["MERGING_DUP_THRESHOLD"]:
            # note: the following status will not be saved in the bib file but
            # in the duplicate_tuples.csv (which will be applied to the bib file
            # in the end)
            ID = references.loc[references["similarity"].idxmax()]["ID"]
            DEDUPE.REVIEW_MANAGER.logger.debug(
                f"max_similarity ({max_similarity}): {batch_item['record']} {ID}"
            )
            details = references.loc[references["similarity"].idxmax()]["details"]
            DEDUPE.REVIEW_MANAGER.logger.debug(details)
            msg = (
                f'Dropped duplicate: {batch_item["record"]} (duplicate of {ID})'
                + f" (similarity: {max_similarity})\nDetails: {details}"
            )
            DEDUPE.REVIEW_MANAGER.report_logger.info(msg)
            DEDUPE.REVIEW_MANAGER.logger.info(msg)
            ret = {
                "ID1": batch_item["record"],
                "ID2": ID,
                "similarity": max_similarity,
                "decision": "duplicate",
            }
        return ret

    # TODO : add similarity function as a parameter?
    def run_dedupe(self, DEDUPE):
        """Pairwise identification of duplicates based on static similarity measure

        This procedure should only be used in small samples on which active learning
        models cannot be trained.
        """

        import pandas as pd
        from colrev_core.record import RecordState

        pd.options.mode.chained_assignment = None  # default='warn'

        MERGING_NON_DUP_THRESHOLD: float = 0.7
        MERGING_DUP_THRESHOLD: float = 0.95

        saved_args = locals()

        DEDUPE.REVIEW_MANAGER.logger.info(
            "Simple duplicate identification "
            "(not enough records to train active learning model)"
        )

        DEDUPE.REVIEW_MANAGER.logger.info(
            "Pairwise identification of duplicates based on static similarity measure"
        )

        # Note: this would also be a place to set
        # records as "no-duplicate" by definition
        # (e.g., for non-duplicated sources marked in the sources)

        record_state_list = DEDUPE.REVIEW_MANAGER.REVIEW_DATASET.get_record_state_list()
        IDs_to_dedupe = [
            x["ID"]
            for x in record_state_list
            if x["colrev_status"] == str(RecordState.md_prepared)
        ]
        processed_IDs = [
            x["ID"]
            for x in record_state_list
            if x["colrev_status"]
            not in [
                str(RecordState.md_imported),
                str(RecordState.md_prepared),
                str(RecordState.md_needs_manual_preparation),
            ]
        ]

        nr_tasks = len(IDs_to_dedupe)
        dedupe_data = {
            "nr_tasks": nr_tasks,
            "queue": processed_IDs + IDs_to_dedupe,
            "items_start": len(processed_IDs),
        }
        DEDUPE.REVIEW_MANAGER.logger.debug(
            DEDUPE.REVIEW_MANAGER.pp.pformat(dedupe_data)
        )

        # the queue (order) matters for the incremental merging (make sure that each
        # additional record is compared to/merged with all prior records in
        # the queue)

        records = DEDUPE.REVIEW_MANAGER.REVIEW_DATASET.load_records_dict()

        # Note: Because we only introduce individual (non-merged records),
        # there should be no semicolons in colrev_origin!
        records_queue = [
            record
            for ID, record in records.items()
            if ID in dedupe_data["queue"]  # type: ignore
        ]

        records_queue = pd.DataFrame.from_dict(records_queue)
        references = DEDUPE.prep_references(references=records_queue)
        # DEDUPE.REVIEW_MANAGER.pp.pprint(references.values())
        references_df = pd.DataFrame(references.values())

        items_start = dedupe_data["items_start"]
        batch_data = []
        for i in range(items_start, len(dedupe_data["queue"])):  # type: ignore
            batch_data.append(
                {
                    "record": dedupe_data["queue"][i],  # type: ignore
                    "queue": references_df.iloc[: i + 1],
                    "MERGING_NON_DUP_THRESHOLD": MERGING_NON_DUP_THRESHOLD,
                    "MERGING_DUP_THRESHOLD": MERGING_DUP_THRESHOLD,
                }
            )

        dedupe_batch_results = []
        for item in batch_data:
            dedupe_batch_results.append(
                self.append_merges(DEDUPE=DEDUPE, batch_item=item)
            )

        # dedupe_batch[-1]['queue'].to_csv('last_references.csv')

        DEDUPE.apply_merges(results=dedupe_batch_results)

        DEDUPE.REVIEW_MANAGER.logger.info("Completed application of merges")

        DEDUPE.potential_duplicates = [
            r for r in dedupe_batch_results if "potential_duplicate" == r["decision"]
        ]

        records = DEDUPE.REVIEW_MANAGER.REVIEW_DATASET.load_records_dict()

        records_queue = pd.DataFrame.from_dict(records.values())
        references = DEDUPE.prep_references(references=records_queue)
        # DEDUPE.REVIEW_MANAGER.pp.pprint(references.values())
        references = pd.DataFrame(references.values())

        DEDUPE.dedupe_references = references

        DEDUPE.REVIEW_MANAGER.create_commit(
            msg="Process duplicates", script_call="colrev dedupe", saved_args=saved_args
        )

        # DEDUPE.simple_dedupe()

        print("The following may be called externally (cli)")

        references = DEDUPE.dedupe_references
        keys = list(references.columns)
        for key_to_drop in [
            "ID",
            "colrev_origin",
            "colrev_status",
            "colrev_id",
            "container_title",
        ]:
            if key_to_drop in keys:
                keys.remove(key_to_drop)

        n_match, n_distinct = 0, 0
        for potential_duplicate in DEDUPE.potential_duplicates:
            rec1 = references.loc[references["ID"] == potential_duplicate["ID1"], :]
            rec2 = references.loc[references["ID"] == potential_duplicate["ID2"], :]

            record_pair = [rec1.to_dict("records")[0], rec2.to_dict("records")[0]]

            user_input = console_duplicate_instance_label(
                record_pair, keys, True, "TODO", n_match, n_distinct, None
            )

            # set DEDUPE.potential_duplicates
            if "y" == user_input:
                potential_duplicate["decision"] = "duplicate"
                n_match += 1
            if "n" == user_input:
                potential_duplicate["decision"] = "no_duplicate"
                n_distinct += 1

        # apply:
        DEDUPE.apply_merges(results=DEDUPE.potential_duplicates)

        # add and commit
        DEDUPE.REVIEW_MANAGER.REVIEW_DATASET.add_record_changes()
        DEDUPE.REVIEW_MANAGER.create_commit(
            msg="Manual labeling of remaining duplicate candidates",
            manual_author=False,
            script_call="colrev dedupe",
            saved_args=saved_args,
        )
        return


# # Active-learning deduplication

# # Note: code based on
# # https://github.com/dedupeio/dedupe-examples/blob/master/csv_example/csv_example.py


@zope.interface.implementer(DedupeEndpoint)
class ActiveLearningDedupeTrainingEndpoint:
    def __init__(self, *, SETTINGS):
        self.SETTINGS = from_dict(data_class=DefaultSettings, data=SETTINGS)

    def apply_active_learning(self, *, DEDUPE, results, saved_args):

        DEDUPE.apply_manual_deduplication_decisions(results=results)

        # Using the examples we just labeled, train the deduper and learn
        # blocking predicates
        DEDUPE.deduper.train(recall=0.9, index_predicates=True)
        # print(deduper.data_model._field_comparators)
        # print(deduper.predicates)

        # When finished, save our training to disk
        with open(DEDUPE.training_file, "w") as tf:
            DEDUPE.deduper.write_training(tf)
        DEDUPE.REVIEW_MANAGER.REVIEW_DATASET.add_changes(path=DEDUPE.training_file)

        # Save our weights and predicates to disk.  If the settings file
        # exists, we will skip all the training and learning next time we run
        # this file.
        with open(DEDUPE.settings_file, "wb") as sf:
            DEDUPE.deduper.write_settings(sf)

        DEDUPE.REVIEW_MANAGER.create_commit(
            msg="Labeling of duplicates (active learning)",
            manual_author=True,
            script_call="colrev dedupe",
            saved_args=saved_args,
        )
        # deduper.cleanup_training()

        return

    def setup_active_learning_dedupe(self, *, DEDUPE, retrain: bool, in_memory: bool):
        """Prepare data for active learning setup"""
        import dedupe as dedupe_io
        import random

        logging.getLogger("opensearch").setLevel(logging.ERROR)
        logging.getLogger("dedupe.training").setLevel(logging.WARNING)
        logging.getLogger("dedupe.api").setLevel(logging.WARNING)
        # logging.getLogger("rlr.crossvalidation:optimum").setLevel(logging.WARNING)

        if retrain:
            # Note : removing the training_file would be to start from scratch...
            # self.training_file.unlink(missing_ok=True)
            DEDUPE.settings_file.unlink(missing_ok=True)

        DEDUPE.REVIEW_MANAGER.logger.info("Importing data ...")

        # Possible extension: in the readData, we may want to append the colrev_status
        # to use Gazetteer (dedupe_io) if applicable (no duplicates in pos-md_processed)

        data_d = DEDUPE.readData()

        # to address memory issues, we select a sample from data_d
        # and feed it to prepare_training:
        # https://github.com/dedupeio/dedupe/issues/920

        if not in_memory:
            # Note: we have to make sure that when we sample for training,
            # the not-in-memory mode is used for duplicate clustering
            # otherwise, non-sampled duplicates will not be identified
            max_training_sample_size = min(3000, len(list(data_d.keys())))
            DEDUPE.REVIEW_MANAGER.logger.info(
                f"Selecting a random sample of {max_training_sample_size}"
                " to avoid memory problems"
            )
            # TODO : consider similar proportions of post-md_processed/md_prepared?
            keys = random.sample(list(data_d.keys()), max_training_sample_size)
            data_d = {key: data_d[key] for key in keys}

        self.n_new = len(
            [d for d, v in data_d.items() if "md_prepared" == v["colrev_status"]]
        )

        DEDUPE.REVIEW_MANAGER.logger.debug(DEDUPE.REVIEW_MANAGER.pp.pformat(data_d))

        # def title_corpus():
        #     for record in data_d.values():
        #         yield record["title"]

        # def container_corpus():
        #     for record in data_d.values():
        #         yield record["container_title"]

        # def author_corpus():
        #     for record in data_d.values():
        #         yield record["author"]

        # Training

        # TODO : creating a corpus from all fields may create memory issues...

        # Define the fields dedupe will pay attention to
        fields = [
            {
                "field": "author",
                "type": "String",
                # "corpus": author_corpus(),k
                "has missing": True,
                "crf": True,
            },
            {
                "field": "title",
                "type": "String",
                #  "corpus": title_corpus()
                "crf": True,
            },
            {
                "field": "container_title",
                "variable name": "container_title",
                "type": "ShortString",
                # "corpus": container_corpus(),
                "crf": True,
            },
            {"field": "year", "variable name": "year", "type": "DateTime"},
            {
                "field": "volume",
                "variable name": "volume",
                "type": "ShortString",
                "has missing": True,
            },
            {
                "field": "number",
                "variable name": "number",
                "type": "ShortString",
                "has missing": True,
            },
            {
                "field": "pages",
                "type": "ShortString",
                "has missing": True,
                "crf": True,
            },
            {
                "type": "Interaction",
                "interaction variables": [
                    "container_title",
                    "year",
                    "volume",
                    "number",
                ],
            },
        ]
        # Interactions:
        # https://docs.dedupe.io/en/latest/Variable-definition.html

        # Create a new deduper object and pass our data model to it.
        deduper = dedupe_io.Dedupe(fields)

        # If we have training data saved from a previous run of dedupe,
        # look for it and load it in.
        # __Note:__ if you want to train from scratch, delete the training_file

        try:
            if DEDUPE.training_file.is_file():
                DEDUPE.REVIEW_MANAGER.logger.info(
                    "Reading pre-labeled training data from "
                    f"{DEDUPE.training_file.name} "
                    "and preparing data"
                )
                with open(DEDUPE.training_file, "rb") as f:
                    deduper.prepare_training(data_d, f)
            else:
                deduper.prepare_training(data_d)
        except AttributeError:
            if len(data_d) < 50:
                raise DedupeError(
                    'Sample size too small (use {"endpoint": "simple_dedupe"} instead).'
                )

        # TODO  input('del data_d - check memory')
        del data_d

        DEDUPE.REVIEW_MANAGER.logger.info("Reading and preparation completed.")

        DEDUPE.deduper = deduper
        return

    def adapted_console_label(
        self,
        *,
        DEDUPE,
        manual: bool,
        saved_args: dict,
        max_associations_to_check: int = 1000,
    ):
        """
        Train a matcher instance (Dedupe, RecordLink, or Gazetteer) from the cli.
        Example

        .. code:: python

        > deduper = dedupe.Dedupe(variables)
        > deduper.prepare_training(data)
        > dedupe.console_label(deduper)
        """
        from dedupe._typing import Literal
        from dedupe._typing import TrainingData
        from dedupe._typing import TrainingExample
        from dedupe.core import unique
        from colrev_core.environment import LocalIndex

        from colrev.cli import console_duplicate_instance_label

        DEDUPE.REVIEW_MANAGER.logger.info(
            "Note: duplicate associations available in the LocalIndex "
            "are applied automatically."
        )
        DEDUPE.REVIEW_MANAGER.logger.info("Press Enter to start.")
        input()

        LOCAL_INDEX = LocalIndex()
        finished = False
        use_previous = False
        keys = unique(field.field for field in DEDUPE.deduper.data_model.primary_fields)

        buffer_len = 1  # Max number of previous operations
        examples_buffer: typing.List[
            typing.Tuple[TrainingExample, Literal["match", "distinct", "uncertain"]]
        ] = []
        uncertain_pairs: typing.List[TrainingExample] = []

        manual_dedupe_decision_list = []

        while not finished:

            if use_previous:
                record_pair, _ = examples_buffer.pop(0)
                use_previous = False
            else:
                try:
                    if not uncertain_pairs:
                        uncertain_pairs = DEDUPE.deduper.uncertain_pairs()

                    record_pair = uncertain_pairs.pop()
                except IndexError:
                    break

            n_match = len(DEDUPE.deduper.training_pairs["match"]) + sum(
                label == "match" for _, label in examples_buffer
            )
            n_distinct = len(DEDUPE.deduper.training_pairs["distinct"]) + sum(
                label == "distinct" for _, label in examples_buffer
            )
            if (n_match + n_distinct) > max_associations_to_check:
                finished = True

            user_input = "u"
            if (
                record_pair[0]["colrev_id"] == record_pair[1]["colrev_id"]
                # if any of the colrev_ids NA,
                # we don't know whether we have a duplicate.
                and "NA" != record_pair[0]["colrev_id"]
                and "NA" != record_pair[1]["colrev_id"]
            ):
                user_input = "y"
            else:
                # Check LOCAL_INDEX for duplicate information
                index_dupe_info = LOCAL_INDEX.is_duplicate(
                    record1_colrev_id=record_pair[0]["colrev_id"].split(";"),
                    record2_colrev_id=record_pair[1]["colrev_id"].split(";"),
                )

                user_input = console_duplicate_instance_label(
                    record_pair,
                    keys,
                    manual,
                    index_dupe_info,
                    n_match,
                    n_distinct,
                    examples_buffer,
                )

            if user_input == "y":
                manual_dedupe_decision_list.append(
                    {
                        "ID1": record_pair[0]["ID"],
                        "ID2": record_pair[1]["ID"],
                        "decision": "duplicate",
                    }
                )
                examples_buffer.insert(0, (record_pair, "match"))
                msg = (
                    f"Marked as duplicate: {record_pair[0]['ID']} - "
                    + f"{record_pair[1]['ID']}"
                )
                DEDUPE.REVIEW_MANAGER.report_logger.info(msg)

            elif user_input == "n":
                if not manual:
                    # Ensure that non-dupes do not exceed 3x dupes
                    # (for balanced training data)
                    if n_distinct > n_match * 3:
                        examples_buffer.insert(0, (record_pair, "uncertain"))
                        continue

                manual_dedupe_decision_list.append(
                    {
                        "ID1": record_pair[0]["ID"],
                        "ID2": record_pair[1]["ID"],
                        "decision": "no_duplicate",
                    }
                )
                examples_buffer.insert(0, (record_pair, "distinct"))
                msg = (
                    f"Marked as non-duplicate: {record_pair[0]['ID']}"
                    + f" - {record_pair[1]['ID']}"
                )
                DEDUPE.REVIEW_MANAGER.report_logger.info(msg)

            elif user_input == "u":
                examples_buffer.insert(0, (record_pair, "uncertain"))
            elif user_input == "f":
                os.system("cls" if os.name == "nt" else "clear")
                print("Finished labeling")
                finished = True
            elif user_input == "p":
                use_previous = True
                uncertain_pairs.append(record_pair)

            if len(examples_buffer) > buffer_len:
                record_pair, label = examples_buffer.pop()
                if label in {"distinct", "match"}:
                    examples: TrainingData = {"distinct": [], "match": []}
                    examples[label].append(record_pair)
                    DEDUPE.deduper.mark_pairs(examples)

        for record_pair, label in examples_buffer:
            if label in ["distinct", "match"]:
                examples = {"distinct": [], "match": []}
                examples[label].append(record_pair)
                DEDUPE.deduper.mark_pairs(examples)

        # Note : for debugging:
        # import csv
        # keys = manual_dedupe_decision_list[0].keys()
        # with open("manual_dedupe_decision_list.csv", "w", newline="") as output_file:
        #     dict_writer = csv.DictWriter(output_file, keys)
        #     dict_writer.writeheader()
        #     dict_writer.writerows(manual_dedupe_decision_list)

        # Apply and commit
        self.apply_active_learning(
            DEDUPE=DEDUPE, results=manual_dedupe_decision_list, saved_args=saved_args
        )

        return

    def run_dedupe(self, DEDUPE):

        # def run_active_learning_dedupe(
        #     *,
        #     DEDUPE,
        #     saved_args,
        #     in_memory: bool = True,
        # ) -> None:

        import dedupe as dedupe_io

        # TODO : add something?
        saved_args: dict = {}
        in_memory = True

        self.setup_active_learning_dedupe(
            DEDUPE=DEDUPE, retrain=False, in_memory=in_memory
        )

        # if DEDUPE.skip_training and DEDUPE.settings_file.is_file():
        #     DEDUPE.REVIEW_MANAGER.report_logger\
        #       .info(f"Reading model from {DEDUPE.settings_file.name}")
        #     with open(DEDUPE.settings_file, "rb") as f:
        #         deduper = dedupe.StaticDedupe(f)
        # else:

        # Active learning
        dedupe_io.console_label = self.adapted_console_label
        dedupe_io.console_label(DEDUPE=DEDUPE, manual=True, saved_args=saved_args)

        # Cluster remaining tuples
        # self.cluster_tuples(
        #     in_memory=in_memory,
        #     saved_args=saved_args,
        # )

        return


@dataclass
class ActiveLearningSettings:
    name: str
    merge_threshold: float
    partition_threshold: float


@zope.interface.implementer(DedupeEndpoint)
class ActiveLearningDedupeAutomatedEndpoint:
    def __init__(self, *, SETTINGS):
        self.SETTINGS = from_dict(data_class=ActiveLearningSettings, data=SETTINGS)
        assert self.SETTINGS.merge_threshold >= 0.0
        assert self.SETTINGS.merge_threshold <= 1.0
        assert self.SETTINGS.partition_threshold >= 0.0
        assert self.SETTINGS.partition_threshold <= 1.0

    def run_dedupe(self, DEDUPE):
        """Cluster potential duplicates, merge, and export validation spreadsheets"""

        import statistics
        import dedupe
        import sqlite3
        import psutil

        # Setting in-memory mode depending on system ram

        record_state_list = DEDUPE.REVIEW_MANAGER.REVIEW_DATASET.get_record_state_list()
        sample_size = len(record_state_list)

        ram = psutil.virtual_memory().total
        in_memory = True
        if sample_size * 5000000 > ram:
            # Sample size too large
            in_memory = False

        saved_args: dict = {}

        with open(DEDUPE.settings_file, "rb") as sf:
            deduper = dedupe.StaticDedupe(sf, num_cores=4)

        DEDUPE.REVIEW_MANAGER.logger.info("Clustering duplicates...")

        data_d = DEDUPE.readData()
        DEDUPE.REVIEW_MANAGER.logger.info(
            f"Number of records (before): {len(data_d.items())}"
        )

        # `partition` will return sets of records that dedupe
        # believes are all referring to the same entity.

        saved_args.update(merge_threshold=str(self.merge_threshold))
        saved_args.update(partition_threshold=str(self.partition_threshold))

        if in_memory:
            DEDUPE.REVIEW_MANAGER.report_logger.info(
                f"set partition_threshold: {self.partition_threshold}"
            )

            clustered_dupes = deduper.partition(data_d, self.partition_threshold)

            # from dedupe.core import BlockingError
            # except BlockingError:

            #     DEDUPE.REVIEW_MANAGER.logger.info(
            #         "No duplicates found (please check carefully)"
            #     )
            #     DEDUPE.apply_merges(results=[], remaining_non_dupe=True)
            #     DEDUPE.REVIEW_MANAGER.create_commit(
            #         msg="Deduplication (no duplicates detected)",
            #         script_call="colrev dedupe",
            #         saved_args=saved_args,
            #     )
            #     DEDUPE.REVIEW_MANAGER.logger.info(
            #         "If there are errors, it could be necessary to remove the "
            #         ".references_dedupe_training.json to train a fresh dedupe model."
            #     )

            #     pass
            # except KeyboardInterrupt:
            #     print("KeyboardInterrupt")
            #     pass

        else:

            for field in deduper.fingerprinter.index_fields:
                field_data = (r[field] for r in data_d.values() if field in r)
                deduper.fingerprinter.index(field_data, field)

            full_data = ((r["ID"], r) for r in data_d.values())
            b_data = deduper.fingerprinter(full_data)

            # use sqlite: light-weight, file-based
            # https://docs.python.org/3/library/sqlite3.html
            # https://dedupeio.github.io/dedupe-examples/docs/pgsql_big_dedupe_example.html

            dedupe_db = Path("dedupe.db")
            dedupe_db.unlink(missing_ok=True)
            con = sqlite3.connect(str(dedupe_db))

            cur = con.cursor()

            cur.execute("""DROP TABLE IF EXISTS blocking_map""")
            cur.execute("""CREATE TABLE blocking_map (block_key text, ID INTEGER)""")
            cur.executemany("""INSERT into blocking_map values (?, ?)""", b_data)

            records_data = {r["ID"]: r for r in data_d.values()}

            def record_pairs(result_set):

                for i, row in enumerate(result_set):
                    ID_a, ID_b = row
                    record_a = (ID_a, records_data[ID_a])
                    record_b = (ID_b, records_data[ID_b])

                    yield record_a, record_b

            cur.execute(
                """select DISTINCT l.ID as east, r.ID as west
                        from blocking_map as l
                        INNER JOIN blocking_map as r
                        using (block_key)
                        where east != west"""
            )

            clustered_dupes = list(
                deduper.cluster(
                    deduper.score(record_pairs(cur.fetchall())), threshold=0.5
                )
            )

            # import csv
            # clusterin_results_csv = Path("clusterin_results.csv")
            # clusterin_results_csv.unlink(missing_ok=True)
            # with open(clusterin_results_csv, "w") as out:
            #     csv_out = csv.writer(out)
            #     csv_out.writerow(["ID1", "ID2", "conf"])
            #     for row in list(cluster_ids(clustered_dupes)):
            #         if row[0] != row[1]:  # only focus on non-identical IDs
            #             csv_out.writerow(row)

            con.commit()
            con.close()
            dedupe_db.unlink(missing_ok=True)

        DEDUPE.REVIEW_MANAGER.report_logger.info(
            f"Number of duplicate sets {len(clustered_dupes)}"
        )
        # Results
        cluster_membership = {}
        dedupe_decision_list = []
        for cluster_id, (records, scores) in enumerate(clustered_dupes):
            dedupe_decision_list.append(
                {
                    "cluster_id": cluster_id,
                    "records": list(records),
                    "score": statistics.mean(list(scores)),
                }
            )
            for record_id, score in zip(records, scores):

                cluster_membership[record_id] = {
                    "cluster_id": cluster_id,
                    "confidence_score": score,
                }

        # cluster_membership:
        # {'FrolovaFrolovKayurovEtAl2021': {'Cluster ID': 352, 'confidence_score': 1.0},
        #  'BhaskaraBawa2021': {'Cluster ID': 353, 'confidence_score': 1.0}}

        auto_dedupe = []
        ID_list = []
        DEDUPE.REVIEW_MANAGER.report_logger.info(
            f"set merge_threshold: {self.merge_threshold}"
        )
        DEDUPE.REVIEW_MANAGER.logger.info(
            f"set merge_threshold: {self.merge_threshold}"
        )
        for dedupe_decision in dedupe_decision_list:

            if len(dedupe_decision["records"]) > 1:
                if dedupe_decision["score"] > self.merge_threshold:
                    orig_rec = dedupe_decision["records"].pop()
                    ID_list.append(orig_rec)
                    if 0 == len(dedupe_decision["records"]):
                        auto_dedupe.append(
                            {
                                "ID1": orig_rec,
                                "decision": "no_duplicate",
                            }
                        )
                        continue

                    for dupe_rec in dedupe_decision["records"]:

                        orig_propagated = (
                            DEDUPE.REVIEW_MANAGER.REVIEW_DATASET.propagated_ID(
                                ID=orig_rec
                            )
                        )
                        dupe_propagated = (
                            DEDUPE.REVIEW_MANAGER.REVIEW_DATASET.propagated_ID(
                                ID=dupe_rec
                            )
                        )

                        if not orig_propagated and not dupe_propagated:

                            # Use the record['ID'] without appended letters if possible
                            # Set orig_propagated=True if record_a_ID should be kept
                            if (
                                orig_rec[-1:].isnumeric()
                                and not dupe_rec[-1:].isnumeric()
                            ):
                                orig_propagated = True
                            else:
                                dupe_propagated = True
                                # This arbitrarily uses record_b_ID
                                # if none of the IDs has a letter appended.

                            if orig_propagated and dupe_propagated:
                                # both_IDs_propagated
                                DEDUPE.REVIEW_MANAGER.logger.error(
                                    f"Both IDs propagated: {orig_rec}, {dupe_rec}"
                                )
                                continue

                            if orig_propagated:
                                auto_dedupe.append(
                                    {
                                        "ID1": orig_rec,
                                        "ID2": dupe_rec,
                                        "decision": "duplicate",
                                        "score": dedupe_decision["score"],
                                    }
                                )

                            else:
                                auto_dedupe.append(
                                    {
                                        "ID1": dupe_rec,
                                        "ID2": orig_rec,
                                        "decision": "duplicate",
                                        "score": dedupe_decision["score"],
                                    }
                                )

        DEDUPE.apply_merges(results=auto_dedupe, remaining_non_dupe=True)

        DEDUPE.REVIEW_MANAGER.reorder_log(
            IDs=ID_list, criterion="descending_thresholds"
        )

        # Export excels for validation
        def highlight_cells(x):
            df = x.copy()
            df["cluster_id"] = df["cluster_id"].astype(str)
            df.loc[:, df.columns != "cluster_id"] = "background-color: white"

            # http://www.excelsupersite.com/what-are-the-56-colorindex-colors-in-excel/
            available_colors = [
                "#FFFFFF",
                "#FFCC99",
                "#FFFFCC",
                "#CCFFCC",
                "#FFFF99",
                "#99CCFF",
                "#FF99CC",
            ]
            cur_color_index = -1
            cur_cluster = ""

            prev_row = []
            for i, row in df.iterrows():
                if row["cluster_id"] != cur_cluster:
                    cur_color_index += 1
                    cur_cluster = row["cluster_id"]
                # df.at[i, 'cluster_id'] = ( # only the cluster_id column
                df.at[i, :] = (
                    "background-color: "
                    + available_colors[cur_color_index % len(available_colors)]
                )

            for i, row in x.iterrows():
                if i == 0 or i == 1:
                    continue
                if len(prev_row) != 0:
                    for j, val in row.items():
                        # changes in these fields should not be marked
                        if j in ["error", "confidence_score", "ID"]:
                            continue
                        # do not mark changes between different clusters
                        if j == "cluster_id" and prev_row["cluster_id"] != val:
                            break
                        if val != prev_row[j]:
                            df.at[i, j] = df.at[i, j] + "; font-weight: bold"

                prev_row = row

            return df

        collected_duplicates = []
        collected_non_duplicates = []
        for ID, vals in data_d.items():
            vals.update(error="")
            if ID in cluster_membership:
                cur_cluster_membership = cluster_membership[ID]
                vals.update(cur_cluster_membership)
                if cur_cluster_membership["confidence_score"] > self.merge_threshold:
                    collected_duplicates.append(vals)
                else:
                    collected_non_duplicates.append(vals)

        # Set confidence scores to average of group
        for cluster_nr in {d["cluster_id"] for d in collected_duplicates}:
            avg_confidence = statistics.mean(
                [
                    d["confidence_score"]
                    for d in collected_duplicates
                    if d["cluster_id"] == cluster_nr
                ]
            )
            for collected_duplicate in collected_duplicates:
                if collected_duplicate["cluster_id"] == cluster_nr:
                    collected_duplicate["confidence_score"] = avg_confidence
        for cluster_nr in {d["cluster_id"] for d in collected_non_duplicates}:
            avg_confidence = statistics.mean(
                [
                    d["confidence_score"]
                    for d in collected_non_duplicates
                    if d["cluster_id"] == cluster_nr
                ]
            )
            for collected_non_duplicate in collected_non_duplicates:
                if collected_non_duplicate["cluster_id"] == cluster_nr:
                    collected_non_duplicate["confidence_score"] = avg_confidence

        if len(collected_duplicates) == 0:
            print("No duplicates found")
        else:
            duplicates_df = pd.DataFrame.from_records(collected_duplicates)
            duplicates_df.fillna("", inplace=True)
            duplicates_df["distinct_str"] = (
                duplicates_df["author"]
                + duplicates_df["title"]
                + duplicates_df["year"]
                + duplicates_df["container_title"]
                + duplicates_df["volume"]
                + duplicates_df["number"]
                + duplicates_df["pages"]
            )
            # Only export bibliographically distict cases
            duplicates_df = duplicates_df.groupby("distinct_str").filter(
                lambda x: len(x) == 1
            )
            duplicates_df.drop(columns=["distinct_str"], inplace=True)

            duplicates_df = duplicates_df[
                [
                    "error",
                    "confidence_score",
                    "cluster_id",
                    "ID",
                    "author",
                    "title",
                    "year",
                    "container_title",
                    "volume",
                    "number",
                    "pages",
                ]
            ]

            duplicates_df = duplicates_df.groupby("cluster_id").filter(
                lambda x: len(x) > 1
            )
            duplicates_df = duplicates_df.sort_values(
                ["confidence_score", "cluster_id"], ascending=(True, False)
            )
            duplicates_df["confidence_score"] = duplicates_df["confidence_score"].round(
                4
            )
            # to adjust column widths in ExcelWriter:
            # http://pandas-docs.github.io/pandas-docs-travis/user_guide/style.html
            duplicates_df = duplicates_df.style.apply(highlight_cells, axis=None)
            duplicates_df.to_excel(DEDUPE.dupe_file, index=False)

            if len(collected_non_duplicates) > 0:
                non_duplicates_df = pd.DataFrame.from_records(collected_non_duplicates)
                # To develop in jupyter:
                # non_duplicates_df.to_csv(output_file, index=False)
                # non_duplicates_df = pd.read_csv("duplicates_for_validation.csv")
                non_duplicates_df = non_duplicates_df[
                    [
                        "error",
                        "cluster_id",
                        "confidence_score",
                        "ID",
                        "author",
                        "title",
                        "year",
                        "container_title",
                        "volume",
                        "number",
                        "pages",
                    ]
                ]
                non_duplicates_df = non_duplicates_df.groupby("cluster_id").filter(
                    lambda x: len(x) > 1
                )
                non_duplicates_df = non_duplicates_df.sort_values(
                    ["confidence_score", "cluster_id"], ascending=(True, False)
                )
                non_duplicates_df["confidence_score"] = non_duplicates_df[
                    "confidence_score"
                ].round(4)
                # to adjust column widths in ExcelWriter:
                # http://pandas-docs.github.io/pandas-docs-travis/user_guide/style.html
                non_duplicates_df = non_duplicates_df.style.apply(
                    highlight_cells, axis=None
                )
                non_duplicates_df.to_excel(DEDUPE.non_dupe_file_xlsx, index=False)

        DEDUPE.REVIEW_MANAGER.create_commit(
            msg="Deduplication (based on active-learning clusters)",
            script_call="colrev dedupe",
            saved_args=saved_args,
        )

        DEDUPE.REVIEW_MANAGER.logger.info(
            "Successfully completed the deduplication. Please check the "
            "duplicates_to_validate.xlsx and non_duplicates_to_validate.xlsx for "
            'potential errors.\nTo fix them, mark them in the "error" column and '
            "run\n  colrev dedupe --fix_errors\n\n"
        )

        if Path("same_source_merges.txt").is_file():
            DEDUPE.REVIEW_MANAGER.logger.info(
                "Detected and prevented same-source merges. Please check potential"
                "duplicates in same_source_merges.txt"
            )

        info = DEDUPE.get_info()
        if len(info["same_source_merges"]) > 0:
            DEDUPE.REVIEW_MANAGER.logger.info(
                f"\n{colors.ORANGE}Same source merges to check:{colors.END}"
                "\n- ".join(info["same_source_merges"]) + "\n"
            )
        else:
            DEDUPE.REVIEW_MANAGER.logger.info("\nNo same-origin merges detected.")

        return
