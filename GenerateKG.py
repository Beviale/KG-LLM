from dotenv import load_dotenv
import json
from graphrag_sdk.source import URL
from graphrag_sdk import KnowledgeGraph, Ontology
from graphrag_sdk.helpers import extract_json
import litellm
from litellm import completion, ContextWindowExceededError
from graphrag_sdk.model_config import KnowledgeGraphModelConfig
from graphrag_sdk.source import TEXT
from pypdf import PdfReader
import unicodedata
import re
from pathlib import Path
from graphrag_sdk.models.litellm import LiteModel
import Prompt
import os
from falkordb import FalkorDB
from graphrag_sdk.steps import extract_data_step
import spacy


load_dotenv()
host = os.getenv("FALKORDB_HOST", "localhost")
port = int(os.getenv("FALKORDB_PORT", "6379"))
username = os.getenv("FALKORDB_USERNAME", "beviale")  
password = os.getenv("FALKORDB_PASSWORD", "12345678Ab!") 

client_kwargs = {
    "host": host,
    "port": port,
}



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


def process_response_ontology(text_filename, category, index_chunk, response, model):
    response_content = response.choices[0].message["content"]       
    response_content = response_content.strip()

    try:
        data = json.loads(extract_json(response_content))
        _ = Ontology.from_json(data)
    except Exception as e:
        # fallback 
        print(f"Error extracting JSON: {e}")
        print(f"Prompting model to fix JSON")
        if isinstance(e, json.JSONDecodeError):
            json_fix_response = completion(
                model=model,
                messages=[
                    {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                    {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_ITA.format(error=str(e), json=response_content)}         
                ]
            )
        else:
                json_fix_response = completion(
                model=model,
                messages=[
                    {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                    {"role": "user",   "content": Prompt.FIX_ONTOLOGY_PROMPT_ITA.format(ontology=response_content, errors=str(e))}         
                ]
            )

        json_fix_response_content = json_fix_response.choices[0].message["content"]
        try:
            data = json.loads(extract_json(json_fix_response_content))
            _ = Ontology.from_json(data)
            print(f"JSON fixed!")
        except Exception as e:
            print(f"Failed to fix JSON: {e}")
            return

    
    current_ontology_ident = json.dumps(data, indent=2, ensure_ascii=False)
    if current_ontology_ident is not None:
        print(f"Ontology '{text_filename}' created successfully!")
        ontology_file_name = f"Ontologies/{category}/{text_filename}_{index_chunk}_Ontology.json"
        # Save the ontology to the disk as a json file.
        with open(ontology_file_name, "w", encoding="utf-8") as file:
            file.write(current_ontology_ident)


def split_text_chunks(text: str, max_characters=1000):
    nlp = spacy.load("it_core_news_sm")
    doc = nlp(text)
    sentences = [sent.text.strip() for sent in doc.sents]
    sentences = [sentence for sentence in sentences if sentence]
    chunks = []
    chunk = []
    while(True):
        if not sentences:
            break
        maximum_reached = False
        for sentence in sentences:
            chunk.append(sentence)
            number_of_charachters = sum(len(s) for s in chunk)
            if (number_of_charachters>max_characters): 
                maximum_reached = True             
                break
            
        if maximum_reached:
            chunk_reverse = chunk.copy()
            chunk_reverse.reverse()
            number_of_characters = 0
            index = 0
            for chunk_rev in chunk_reverse:
                index = index + 1
                number_of_characters = number_of_characters + len(chunk_rev)
                if (number_of_characters>200):
                    break
            sentences = sentences[len(chunk)-index:]
           
        else:
            break
        chunks.append(" ".join(chunk))
        chunk.clear()
    return chunks
        

def merge_ontologies_chunk(category, text_filename, model=None):
    if model is None:
        model = "openai/gpt-5-mini"
    index = 0
    json_merge = []
    while(True):
        file_path = Path(f"Ontologies/{category}/{text_filename}_{index}_Ontology.json")
        if file_path.exists()==False:
            break
        
        with file_path.open("r", encoding="utf-8") as f:
            json_item = json.load(f)

        json_merge.append(json_item)
        index += 1
    
    new_json = None
    for i in range(0, len(json_merge), 4):
        json_merge_slice = json_merge[i:i+4]
        if new_json is not None:
            json_merge_slice.append(new_json)

        ontologies = ';'.join(
            json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
            for obj in json_merge_slice
        )

        print("Waiting the LLM response to merge the ontologies...")
        response = completion(
            model=model,
            messages=[
                {"role": "system", "content": Prompt.MERGE_ONTOLOGY_SYSTEM_ITA},
                {"role": "user",   "content": Prompt.MERGE_ONTOLOGY_PROMPT_ITA.format(ontologies=ontologies)}
            ]
        ) 
        response_content = response.choices[0].message["content"]       
        response_content = response_content.strip()

        try:
            data = json.loads(extract_json(response_content))
            _ = Ontology.from_json(data)
        except Exception as e:
            # fallback 
            print(f"Error extracting JSON: {e}")
            print(f"Prompting model to fix JSON")
            if isinstance(e, json.JSONDecodeError):
                json_fix_response = completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                        {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_ITA.format(error=str(e), json=response_content)}         
                    ]
                )
            else:
                    json_fix_response = completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                        {"role": "user",   "content": Prompt.FIX_ONTOLOGY_PROMPT_ITA.format(ontology=response_content, errors=str(e))}         
                    ]
                )

            json_fix_response_content = json_fix_response.choices[0].message["content"]
            try:
                data = json.loads(extract_json(json_fix_response_content))
                _ = Ontology.from_json(data)
                print(f"JSON fixed!")
                new_json = data
            except Exception as e:
                print(f"Failed to fix JSON: {e}")
                return
   
    for ent in new_json.get("entities", []):
        ent["text_reference"] = ""

    for rel in new_json.get("relations", []):
        rel["text_reference"] = ""

    current_ontology_ident = json.dumps(new_json, indent=2, ensure_ascii=False)
    if current_ontology_ident is not None:
        print(f"Ontology '{text_filename}' created successfully!")
        ontology_file_name = f"Ontologies/{category}/{text_filename}_Ontology.json"
        with open(ontology_file_name, "w", encoding="utf-8") as file:
            file.write(current_ontology_ident)

    for i in range(index):
        file_path = Path(f"Ontologies/{category}/{text_filename}_{index}_Ontology.json")    
        if os.path.exists(file_path):
            os.remove(file_path)

        



def generate_ontology(category, model=None, dataItems=None):
    if dataItems is not None:
        all_text_paths = [item.text_path for item in dataItems]
    else:
        directory = Path(f"InputPDFtoText/{category}")
        all_text_paths = list(directory.rglob("*.txt"))
    
    if model is None:
        model = "openai/gpt-5-mini"

    count = 1
    for text_path in all_text_paths:
        text_filename = text_path.name.removesuffix(".txt")

        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read()

        print(f"File path: '{text_path}', current state: {count}/{len( all_text_paths)}.")
        count = count + 1

        file = Path(f"Ontologies/{category}/{text_filename}_Ontology.json")
        if file.exists():
           print("This ontology already exists!")


        chunks = split_text_chunks(text)
        index_chunk = 0

        for chunk in chunks:
            textsToProcess = []
            textsToAdd = []
            textsToProcess.append(chunk)
            while(True):
                for text in textsToProcess: 
                    print("Waiting the LLM response...")
                    try:     
                        # We create the ontology
                        response = completion(
                            model=model,
                            messages=[
                                {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                                {"role": "user",   "content": Prompt.CREATE_ONTOLOGY_PROMPT_ITA.format(text=text)}
                            ]
                        )                  
                        process_response_ontology(text_filename, category, index_chunk, response, model)   
                    except ContextWindowExceededError as e:
                        mid = len(text) // 2
                        print(f"Halving the text size from '{len(text)}' to '{mid}'")
                        part1 = text[:mid]
                        part2 = text[mid:]
                        textsToAdd.append(part1)
                        textsToAdd.append(part2)
                        continue

                if textsToAdd:
                    textsToProcess.clear()
                    for textToAdd in textsToAdd:
                        textsToProcess.append(textToAdd)
                    textsToAdd.clear()
                else:
                    break
            index_chunk = index_chunk + 1
        merge_ontologies_chunk()



def generate_data(category: str, model=None, dataItems=None):
    client = FalkorDB(**client_kwargs)
    graph = client.select_graph(category)

    if dataItems is not None:
        all_text_paths = [item.text_path for item in dataItems]
    else:
        directory = Path(f"InputPDFtoText/{category}")
        all_text_paths = list(directory.rglob("*.txt"))
    
    if model is None:
        model = "openai/gpt-5-mini"

    for text_path in all_text_paths:
        text_filename = text_path.name
        text_filename = text_filename.removesuffix(".txt")
        ontology_file = f"Ontologies/{category}/{text_filename}_Ontology.json"
        with open(ontology_file, "r", encoding="utf-8") as file:
            textOntology = file.read()
        try:
            jsonOntology = json.loads(textOntology)
            ontology = Ontology.from_json(jsonOntology)
        except Exception as e:
            print(f"Failed to read the ontology: {e}")
            continue

        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read()

        print("File path: ", text_path)
        print("Waiting the LLM response...")
        
        response = completion(
            model=model,
            messages=[
                {"role": "system", "content": Prompt.EXTRACT_DATA_SYSTEM_ITA},
                {"role": "user",   "content": Prompt.EXTRACT_DATA_PROMPT_ITA.format(ontology=ontology, text=text)}
            ]
        )
        response_content = response.choices[0].message["content"]
        response_content = response_content.strip()
        response_content = unicodedata.normalize('NFD',   response_content)
        response_content =  ''.join(ch for ch in response_content if unicodedata.category(ch) != 'Mn')

        try:
            data = json.loads(extract_json(response_content))
        except Exception as e:
            # fallback 
            print(f"Error extracting JSON: {e}")
            print(f"Prompting model to fix JSON")
            json_fix_response = completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": Prompt.EXTRACT_DATA_SYSTEM_ITA},
                        {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_ITA.format(error=str(e), json=response_content)}         
                    ]
                )
            
            json_fix_response_content = json_fix_response.choices[0].message["content"]
            json_fix_response_content = json_fix_response_content.strip()
            json_fix_response_content = unicodedata.normalize('NFD',  json_fix_response_content)
            json_fix_response_content =  ''.join(ch for ch in  json_fix_response_content if unicodedata.category(ch) != 'Mn')

            try:
                data = json.loads(extract_json(json_fix_response_content))
                print(f"JSON fixed!")
            except Exception as e:
                print(f"Failed to fix JSON: {e}")
                continue


        if "entities" not in data or "relations" not in data:
            print(f"Invalid data format. Missing entities or relations")
            continue
               
        for entity in data["entities"]:
            try:
                extract_data_step.create_entity(graph, entity, ontology)
            except Exception as e:
                print(f"Error creating entity: {e}")
                continue
        print("Entities created correctly!")

        for relation in data["relations"]:
            try:
                extract_data_step.create_relation(graph, relation, ontology)
            except Exception as e:
                print(f"Error creating relation: {e}")
                continue
        print("Relations created correctly!")


def create_dir():
    os.makedirs("Ontologies",  exist_ok=True)    
    os.makedirs("Ontologies/DisciplinaDiUtilizzo",  exist_ok=True)    
    os.makedirs("Ontologies/Normativa",  exist_ok=True)    
    os.makedirs("Ontologies/FAQ",  exist_ok=True)    
    os.makedirs("Ontologies/GuidePratiche",  exist_ok=True) 

    os.makedirs("InputPDFtoText",  exist_ok=True)   
    os.makedirs("InputPDFtoText/DisciplinaDiUtilizzo",  exist_ok=True)    
    os.makedirs("InputPDFtoText/Normativa",  exist_ok=True)    
    os.makedirs("InputPDFtoText/FAQ",  exist_ok=True)    
    os.makedirs("InputPDFtoText/GuidePratiche",  exist_ok=True) 



def main():
    create_dir()
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
        print("---------------------------")
        print("1. Preprocess all the .pdf files converting them to .txt files")
        print("2. Generate the ontologies")
        print("3. Load the data")
        print("4. Exit")
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
            while(True):
                print("1. DisciplinaDiUtilizzo")
                print("2. GuidePratiche")
                print("3. Normativa")
                print("4. FAQ")
                choice_cat = int(input("For which category do you want to generate the data? "))
                if choice_cat == 1:
                    generate_data("DisciplinaDiUtilizzo")
                    break
                elif choice_cat == 2:
                    generate_data("GuidePratiche")
                    break
                elif choice_cat == 3:
                    generate_data("Normativa")
                    break
                elif choice_cat == 4:
                    generate_data("FAQ")
                    break
                else:
                    print("Invalid choice. Please try again")
        elif choice == 4:
            break
        else:
            print("Invalid choice. Please try again")
        


if __name__ == "__main__":
    main()

