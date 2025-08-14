import os
from typing import List, Tuple

class CacheManager:
    """Manages cache files for the application"""
    
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.doc_dir = os.path.join(base_dir, 'doc')
        self.output_dir = os.path.join(base_dir, 'output')
        
    def clean_cache(self) -> Tuple[bool, List[str], str]:
        """
        Clean all cached files
        Returns: (success, cleaned_files, error_message)
        """
        cleaned_files = []
        try:
            # Clean doc directory
            if os.path.exists(self.doc_dir):
                for file in os.listdir(self.doc_dir):
                    file_path = os.path.join(self.doc_dir, file)
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            cleaned_files.append(f"doc/{file}")
                    except Exception as e:
                        print(f"Error removing file {file}: {str(e)}")

            # Clean output directory
            if os.path.exists(self.output_dir):
                for file in os.listdir(self.output_dir):
                    file_path = os.path.join(self.output_dir, file)
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            cleaned_files.append(f"output/{file}")
                    except Exception as e:
                        print(f"Error removing file {file}: {str(e)}")

            return True, cleaned_files, ""

        except Exception as e:
            return False, cleaned_files, str(e)

    def clean_specific_cache(self, filename: str) -> Tuple[bool, List[str], str]:
        """
        Clean specific cached files
        Returns: (success, cleaned_files, error_message)
        """
        cleaned_files = []
        try:
            # Get base filename without extension
            base_name = os.path.splitext(filename)[0]
            
            # Clean from doc directory
            pdf_path = os.path.join(self.doc_dir, filename)
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
                cleaned_files.append(f"doc/{filename}")

            # Clean from output directory
            json_path = os.path.join(self.output_dir, f"{base_name}_data.json")
            if os.path.exists(json_path):
                os.remove(json_path)
                cleaned_files.append(f"output/{base_name}_data.json")

            return True, cleaned_files, ""

        except Exception as e:
            return False, cleaned_files, str(e)