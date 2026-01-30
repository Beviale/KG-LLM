from dotenv import load_dotenv
import json
from graphrag_sdk.source import URL
from graphrag_sdk import KnowledgeGraph, Ontology
from graphrag_sdk.models.litellm import LiteModel
from graphrag_sdk.model_config import KnowledgeGraphModelConfig
from graphrag_sdk.source import TEXT
from pypdf import PdfReader
import unicodedata
import re
from pathlib import Path

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
    word = match.group(0)
    exclude_pattern = re.compile(r'[A-Z\']+')
    if exclude_pattern.fullmatch(word):
        return word
    return word.lower()


def preprocess(text: str):
    # 1. We transform all the characters in lowercase 
    new_text = text
    # 2. We normalize unicode (accents, apostrophes, etc.)
    new_text = unicodedata.normalize("NFKC", new_text) 
    # 3. We remove the special characters
    new_text = ''.join(c for c in new_text if c.isprintable() or c in '\n\t')
    new_text = re.sub(r'\s+([.,;:!?])', r'\1', new_text)
    new_text = re.sub(r'(\w+)\s*-\s*(\w+)', r'\1-\2', new_text)
    new_text = re.sub(r'(\r?\n){3,}', r'\n\n', new_text)
    word_pattern = re.compile(r'\b[\w\']+\b')
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
    pdf_path_split = pdf_path.split("\\")
    pdf_name = pdf_path_split[len(pdf_path_split) - 1].removesuffix(".pdf")
    pdf_text_path = r"InputPDFtoText/" + category + r"/" + pdf_name + ".txt"
    with open(pdf_text_path, "w", encoding="utf-8") as f:
        f.write(pdf_text)
    return pdf_text_path



def preprocess_pdf(pdf_dict):
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


    sources = [TEXT(text_path) for text_path in all_text_paths]

    model = LiteModel(model_name="openai/gpt-4.1-nano")

    # Ontology Auto-Detection
    ontology = Ontology.from_sources(
        sources=sources,
        model=model,     
    )
    # Save the ontology to the disk as a json file.
    with open("ontology.json", "w", encoding="utf-8") as file:
        file.write(json.dumps(ontology.to_json(), indent=2))


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

