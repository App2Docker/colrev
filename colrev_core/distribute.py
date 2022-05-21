#! /usr/bin/env python
import shutil
from pathlib import Path

from colrev_core.process import Process
from colrev_core.process import ProcessType


class Distribute(Process):
    def __init__(self, *, REVIEW_MANAGER):
        super().__init__(
            REVIEW_MANAGER=REVIEW_MANAGER,
            type=ProcessType.explore,
            notify_state_transition_process=False,
        )

    def main(self, *, path_str: str, target: Path) -> None:
        from colrev_core.environment import TEIParser, GrobidService

        # if no options are given, take the current path/repo
        # optional: target-repo-path
        # path_str: could also be a url
        # option: chdir (to target repo)?
        # file: copy or move?

        def get_last_ID(bib_file: Path) -> str:
            current_ID = "1"
            if bib_file.is_file():
                with open(bib_file, encoding="utf8") as f:
                    line = f.readline()
                    while line:
                        if "@" in line[:3]:
                            current_ID = line[line.find("{") + 1 : line.rfind(",")]
                        line = f.readline()
            return current_ID

        path = Path.cwd() / Path(path_str)
        if path.is_file():
            if path.suffix == ".pdf":
                GROBID_SERVICE = GrobidService()
                GROBID_SERVICE.start()
                TEI_INSTANCE = TEIParser(
                    self.REVIEW_MANAGER,
                    path,
                )
                record = TEI_INSTANCE.get_metadata()

                target_pdf_path = target / "pdfs" / path.name
                target_pdf_path.parent.mkdir(parents=True, exist_ok=True)
                self.REVIEW_MANAGER.logger.info(f"Copy PDF to {target_pdf_path}")
                shutil.copyfile(path, target_pdf_path)

                self.REVIEW_MANAGER.logger.info(
                    f"append {self.REVIEW_MANAGER.pp.pformat(record)} "
                    "to search/local_import.bib"
                )
                target_bib_file = target / "search/local_import.bib"
                self.REVIEW_MANAGER.logger.info(f"target_bib_file: {target_bib_file}")
                if target_bib_file.is_file():
                    with open(target_bib_file, encoding="utf8") as target_bib:
                        import_records_dict = (
                            self.REVIEW_MANAGER.REVIEW_DATASET.load_records_dict(
                                load_str=target_bib.read()
                            )
                        )
                        import_records = import_records_dict.values()
                else:
                    import_records = []
                    new_record = {
                        "filename": str(target_bib_file.name),
                        "search_type": "OTHER",
                        "source_identifier": "Local import",
                        "search_parameters": "",
                        "comment": "",
                    }

                    sources = self.REVIEW_MANAGER.REVIEW_DATASET.load_sources()
                    sources.append(new_record)
                    self.REVIEW_MANAGER.REVIEW_DATASET.save_sources(sources)

                if 0 != len(import_records):
                    ID = int(get_last_ID(target_bib_file))
                    ID += 1
                else:
                    ID = 1

                record["ID"] = f"{ID}".rjust(10, "0")
                record.update(file=str(target_pdf_path))
                import_records.append(record)

                import_records_dict = {r["ID"]: r for r in import_records}
                self.REVIEW_MANAGER.REVIEW_DATASET.save_records_dict_to_file(
                    import_records_dict, save_path=target_bib_file
                )

                self.REVIEW_MANAGER.REVIEW_DATASET.add_changes(
                    path=str(target_bib_file)
                )

        return


if __name__ == "__main__":
    pass
