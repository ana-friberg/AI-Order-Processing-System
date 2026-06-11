import os
from typing import List, Tuple

from .logging_config import get_logger

logger = get_logger(__name__)


class CacheManager:
    """Remove temporary files from the doc/ and output/ directories."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.doc_dir = os.path.join(base_dir, "doc")
        self.output_dir = os.path.join(base_dir, "output")

    def clean_cache(self) -> Tuple[bool, List[str], str]:
        """Remove all files in doc/ and output/. Returns (success, cleaned_files, error)."""
        cleaned_files: List[str] = []
        try:
            for folder, label in [(self.doc_dir, "doc"), (self.output_dir, "output")]:
                if not os.path.exists(folder):
                    continue
                for filename in os.listdir(folder):
                    filepath = os.path.join(folder, filename)
                    try:
                        if os.path.isfile(filepath):
                            os.remove(filepath)
                            cleaned_files.append(f"{label}/{filename}")
                    except Exception as e:
                        logger.warning("Could not remove %s: %s", filepath, e)
            return True, cleaned_files, ""
        except Exception as e:
            return False, cleaned_files, str(e)

    def clean_specific_cache(self, filename: str) -> Tuple[bool, List[str], str]:
        """Remove cached files for a specific filename. Returns (success, cleaned_files, error)."""
        cleaned_files: List[str] = []
        try:
            # Reject filenames with path separators to prevent traversal
            if os.path.sep in filename or "/" in filename or "\\" in filename or ".." in filename:
                return False, [], "Invalid filename"
            base_name = os.path.splitext(filename)[0]

            pdf_path = os.path.realpath(os.path.join(self.doc_dir, filename))
            doc_dir_real = os.path.realpath(self.doc_dir)
            if not pdf_path.startswith(doc_dir_real + os.sep) and pdf_path != doc_dir_real:
                return False, [], "Invalid filename"
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
                cleaned_files.append(f"doc/{filename}")

            json_filename = f"{base_name}_data.json"
            json_path = os.path.realpath(os.path.join(self.output_dir, json_filename))
            output_dir_real = os.path.realpath(self.output_dir)
            if json_path.startswith(output_dir_real + os.sep) or json_path == output_dir_real:
                if os.path.exists(json_path):
                    os.remove(json_path)
                    cleaned_files.append(f"output/{json_filename}")

            return True, cleaned_files, ""
        except Exception as e:
            return False, cleaned_files, str(e)
