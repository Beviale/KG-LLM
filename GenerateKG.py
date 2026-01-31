from dotenv import load_dotenv
import json
from graphrag_sdk.source import URL
from graphrag_sdk import KnowledgeGraph, Ontology
from graphrag_sdk.helpers import extract_json
import litellm
from litellm import completion
from graphrag_sdk.model_config import KnowledgeGraphModelConfig
from graphrag_sdk.source import TEXT
from pypdf import PdfReader
import unicodedata
import re
from pathlib import Path
import Prompt

load_dotenv()

class DataItem:
    """
    Reperesents a PDF document to process
    """
    def __init__(self, category, pdf_path):
        self.pdf_path = pdf_path
        self.category = category
        self._text = None
        self._text_path = None
        self._num_pages = None

    
    @property
    def text(self):
        if self._text == None:
            self._text, self._num_pages = retrieve_text_from_pdf(self.pdf_path)
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

         
    @property
    def num_pages(self):
        if self._num_pages == None:
            self._text, self._num_pages = retrieve_text_from_pdf(self.pdf_path)
        return self._num_pages

    @num_pages.setter
    def num_pages(self, value):
        self._num_pages = value

        

    @property
    def text_path(self):
        if self._text_path == None:
           self._text_path = save_text(self.category, str(self.pdf_path), self.text)
        return self._text_path

    @text_path.setter
    def text_path(self, value):
       self._text_path = value

    
def selective_lower(match):
    """
    A function that, given a word match, returns the same word if it is an acronym (like 'UE'); otherwise, it returns the word in lowercase.    
    """
    word = match.group(0)
    exclude_pattern = re.compile(r'[A-Z\']+')
    if exclude_pattern.fullmatch(word):
        return word
    return word.lower()


def preprocess(text: str):
    """
    A function that processes the given text extracted from a PDF file, cleaning out unwanted characters and minimizing the number of tokens required for the LLM API call.       
    """
    new_text = text
    # 1. We normalize unicode (accents, apostrophes, etc.)
    new_text = unicodedata.normalize("NFKC", new_text) 
    # 2. We remove the special characters
    new_text = ''.join(c for c in new_text if c.isprintable() or c in '\n\t')
    # 3. From "prova :" to-> "prova:"
    new_text = re.sub(r'\s+([.,;:!?])', r'\1', new_text)
    # 4. From "prova1 - prova2" to-> "prova1-prova2"
    new_text = re.sub(r'([A-Za-zÀ-ÿ]+)\s*[-]\s*([A-Za-zÀ-ÿ]+)', r'\1-\2', new_text)
    # 5. We remove the empty rows
    pattern_emty_rows = r'(?:^\s*\r?\n){3,}'
    new_text = re.sub(pattern_emty_rows, r'\r\n', new_text, flags=re.MULTILINE)
    # 6. We transform all the text in lowercase excpet for the acronyms
    word_pattern = re.compile(r'\b[\w]+\b')
    new_text =  word_pattern.sub(selective_lower, new_text)
    return new_text

   

def retrieve_text_from_pdf(pdf_path: str):
    """
        Give a pdf document path, it retrieves the text inside it. 
    """
    num_pages = 0
    pdf_text = ""
    reader = PdfReader(pdf_path)
    for page in reader.pages:
        num_pages = num_pages + 1
        pdf_text = pdf_text + page.extract_text()
    pdf_text = preprocess(pdf_text)
    return pdf_text, num_pages


def save_text(category: str, pdf_path: str, pdf_text: str):
    """
    Given a text extracted from a .pdf file, it creates the corresponding .txt file.
    """
    pdf_path_split = pdf_path.split("\\")
    pdf_name = pdf_path_split[len(pdf_path_split) - 1].removesuffix(".pdf")
    pdf_text_path = r"InputPDFtoText/" + category + r"/" + pdf_name + ".txt"
    with open(pdf_text_path, "w", encoding="utf-8") as f:
        f.write(pdf_text)
    return pdf_text_path



def preprocess_pdf(pdf_dict):
    """
    Given a pdf_dict, it created the corresponding .txt files.
    
    :param pdf_dict:: a dictionary where each key is a category/topic (e.g., "DisciplinaDiUtilizzo") and each value is the list of PDF file paths associated with it.
    """
    dataItems = []
    for category, pdf_paths in pdf_dict.items():
        for pdf_path in pdf_paths:
            dataItem = DataItem(category, pdf_path)
            path = dataItem.text_path
            dataItems.append(dataItem)
            print(f"PDF file \"{pdf_path}\" processed correctly.")
    return dataItems


def generate_ontology(category, dataItems=None):
    if dataItems is not None:
        all_text_paths = [item.text_path for item in dataItems]
    else:
        directory = Path(f"InputPDFtoText/{category}")
        all_text_paths = list(directory.rglob("*.txt"))

    for text_path in all_text_paths:
       
        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read()


        response = completion(
            model="openai/gpt-4.1-nano",
            messages=[
                {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                {"role": "user",   "content": Prompt.CREATE_ONTOLOGY_PROMPT_ITA.format(text=text)}
            ]
        )
        response_content = response.choices[0].message["content"]
        
        response_content = response_content.strip()

        if response_content.startswith("'") and response_content.endswith("'"):
            response_content = response_content[1:-1]

        try:
            data = json.loads(extract_json(response_content))
        except json.decoder.JSONDecodeError as e:
            print(f"Error extracting JSON: {e}")
            print(f"Prompting model to fix JSON")
            json_fix_response = completion(
                model="openai/gpt-4.1-nano",
                messages=[
                    {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                    {"role": "user",   "content": Prompt.FIX_ONTOLOGY_PROMPT_ITA.format(ontology=response_content, errors=str(e))}         
                ]
            )
            json_fix_response_content = json_fix_response.choices[0].message["content"]
            try:
                data = json.loads(extract_json(json_fix_response_content))
                print(f"Fixed JSON: {data}")
            except json.decoder.JSONDecodeError as e:
                print(f"Failed to fix JSON: {e} {json_fix_response_content}")
                data = None
        if data is None:
            continue
        ontology_file_name = category + "_Ontology.json"
        # Save the ontology to the disk as a json file.
        with open(ontology_file_name, "w", encoding="utf-8") as file:
            file.write(json.dumps(data, indent=2))


def main():
    pdf_dict = {} # The key is the category (DiscplinaDiUtilizzo, GuidePratiche,...) and the value is the list of associated .pdf files

    # DiscplinaDiUtilizzo
    dir_DisciplinaDiUtilizzo = Path("InputPDF/DisciplinaDiUtilizzo")
    pdf_paths_DisciplinaDiUtilizzo = list(dir_DisciplinaDiUtilizzo.rglob("*.pdf"))
    pdf_dict["DisciplinaDiUtilizzo"] = pdf_paths_DisciplinaDiUtilizzo

    # GuidePratiche
    dir_GuidePratiche = Path("InputPDF/GuidePratiche")
    pdf_paths_GuidePratiche= list(dir_GuidePratiche.rglob("*.pdf"))
    pdf_dict["GuidePratiche"] = pdf_paths_GuidePratiche

    # Normativa
    dir_Normativa = Path("InputPDF/Normativa")
    pdf_paths_Normativa= list(dir_Normativa.rglob("*.pdf"))
    pdf_dict["Normativa"] = pdf_paths_Normativa


    while(True):
        print("1. Preprocess all the .pdf files converting them to .txt files")
        print("2. Generate the ontologies")
        print("3. Exit")
        choice = int(input("What do you want to do? "))
        if choice == 1:
            dataItems = preprocess_pdf(pdf_dict)
        elif choice == 2:
            while(True):
                print("1. DisciplinaDiUtilizzo")
                print("2. GuidePratiche")
                print("3. Normativa")
                print("4. FAQ")
                choice_cat = int(input("For which category do you want to generate the ontology? "))
                if choice_cat == 1:
                    generate_ontology("DisciplinaDiUtilizzo")
                    break
                elif choice_cat == 2:
                    generate_ontology("GuidePratiche")
                    break
                elif choice_cat == 3:
                    generate_ontology("Normativa")
                    break
                elif choice_cat == 4:
                    generate_ontology("FAQ")
                    break
                else:
                    print("Invalid choice. Please try again")
        elif choice == 3:
            break
        else:
            print("Invalid choice. Please try again")
        


if __name__ == "__main__":
    main()

