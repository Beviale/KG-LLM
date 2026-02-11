from dotenv import load_dotenv
import json
from graphrag_sdk.source import URL
from graphrag_sdk import KnowledgeGraph, Ontology
from graphrag_sdk.helpers import extract_json, map_dict_to_cypher_properties
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
from colorama import init, Fore, Style
from litellm import embedding
import numpy as np
from sklearn.cluster import AgglomerativeClustering


init(autoreset=True)


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
    It processes the given text extracted from a PDF file, cleaning out unwanted characters and minimizing the number of tokens required for the LLM API call.       
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
    Given a PDF document path, it retrieves the text inside it. 
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
    Given a text extracted from a PDF file, it creates the corresponding .txt file.
    """
    pdf_path_split = pdf_path.split("\\")
    pdf_name = pdf_path_split[len(pdf_path_split) - 1].removesuffix(".pdf")
    pdf_text_path = r"InputPDFtoText/" + category + r"/" + pdf_name + ".txt"
    with open(pdf_text_path, "w", encoding="utf-8") as f:
        f.write(pdf_text)
    return pdf_text_path



def preprocess_pdf(pdf_dict):
    """
    Given a pdf_dict, it creates the corresponding .txt files.
    
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


def process_response_ontology(text_filename, category, index_chunk, response, text, model):
    """
    It processes the LLM response to create the ontology for a specific category (e.g., 'DisciplinaDiUtilizzo').
    """
    response_content = response.choices[0].message["content"].strip()  

    while(True):
        json_error=True
        ontology_error=True
        try:
            data = json.loads(extract_json(response_content))
            json_error=False
            _ = Ontology.from_json(data)
            ontology_error=False
            print(f"{Fore.WHITE}Chunk ontology created!")
            break
        except Exception as e:
            try:
                # fallback 
                error = f"TypeError: '{type(e)}', error: '{e}'"
                print(f"Error extracting JSON. TypeError: {error}")
                print(f"Prompting model to fix JSON")
                if json_error:
                    json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_ONTOLOGY_ITA.format(error=error, json=response_content, text=text)}         
                        ]
                    )
                elif ontology_error:
                        json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.FIX_ONTOLOGY_PROMPT_ITA.format(ontology=response_content, errors=error, text=text)}         
                        ]
                    )
                response_content = json_fix_response.choices[0].message["content"].strip()
            except Exception as e:
                print(f"{Fore.RED}Exception throws: {str(e)}")
                continue       

    
    current_ontology_ident = json.dumps(data, indent=2, ensure_ascii=False)
    if current_ontology_ident is not None:
        print(f"Ontology '{text_filename}' with index chunk:'{index_chunk}' created successfully!")
        ontology_file_name = f"Ontologies/{category}/{text_filename}_{index_chunk}_Ontology.json"
        # Save the ontology to the disk as a json file.
        with open(ontology_file_name, "w", encoding="utf-8") as file:
            file.write(current_ontology_ident)



def split_text_chunks(text: str, max_characters=4000):
    """
    It splits the given text into text chunks considering the given maximum number of characters. 
    It returns the list of text chunks. 
    """
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
            sentences = [] 
        chunks.append(" ".join(chunk))
        chunk.clear()
    return chunks
        

def merge_ontologies_chunk(category, text_filename, model=None):
    """
    It merges the ontologies created for each text chunk of a specific category (e.g. 'DisciplinaDiUtilizzo') into a single ontology file.
    """
    if model is None:
        model = "openai/gpt-5-nano"
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
    

    if len(json_merge) <= 1:
        file_path = Path(f"Ontologies/{category}/{text_filename}_0_Ontology.json")    
        file_path_new = Path(f"Ontologies/{category}/{text_filename}_Ontology.json")    
        if os.path.exists(file_path):
            os.rename(file_path, file_path_new)
        return    

    new_json = json_merge[0]
    for i in range(1, len(json_merge)):
        first_ontology =  json.dumps(new_json, ensure_ascii=False)
        second_ontology = json.dumps(json_merge[i], ensure_ascii=False)


        print("Waiting the LLM response to merge the ontologies...")
        response = completion(
            model=model,
            messages=[
                {"role": "system", "content": Prompt.MERGE_ONTOLOGY_SYSTEM_ITA},
                {"role": "user",   "content": Prompt.MERGE_ONTOLOGY_PROMPT_ITA.format(first_ontology=first_ontology, second_ontology=second_ontology)}
            ]
        ) 
        response_content = response.choices[0].message["content"].strip()  

        while(True):
            json_error=True
            ontology_error=True
            try:
                data = json.loads(extract_json(response_content))
                json_error = False
                _ = Ontology.from_json(data)
                ontology_error = False
                new_json = data
                print("Chunk ontologies merged correctly!")
                break
            except Exception as e:
                try:
                    # fallback
                    error = f"TypeError: '{type(e)}', error: '{e}'"
                    print(f"Error extracting JSON. {error}")
                    print(f"Prompting model to fix JSON")
                    if json_error:
                        json_fix_response = completion(
                            model=model,
                            messages=[
                                {"role": "system", "content": Prompt.MERGE_ONTOLOGY_SYSTEM_ITA},
                                {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_ONTOLOGY_MERGE_ITA.format(error=error, json=response_content, first_ontology=first_ontology, second_ontology=second_ontology)}         
                            ]
                        )
                    elif ontology_error:
                            json_fix_response = completion(
                            model=model,
                            messages=[
                                {"role": "system", "content": Prompt.MERGE_ONTOLOGY_SYSTEM_ITA},
                                {"role": "user",   "content": Prompt.FIX_ONTOLOGY_PROMPT_MERGE_ITA.format(ontology=response_content, errors=error, first_ontology=first_ontology, second_ontology=second_ontology)}         
                            ]
                        )

                    response_content = json_fix_response.choices[0].message["content"].strip()
                except Exception as e:
                    print(f"{Fore.RED}Exception throws: {str(e)}")
                    continue
            
   
    new_attr = {
        "name": "riferimentoTestuale",
        "type": "string",
        "unique": False,
        "required": True
    }

    for entity in new_json.get("entities", []): 
        attrs = entity.get("attributes")     
        if attrs is None:
            attrs = []
            entity["attributes"] = attrs
        
        exists = any(a.get("name") == "riferimentoTestuale" for a in attrs)
        if not exists:
            attrs.append(new_attr)

    for relation in new_json.get("relations", []): 
        attrs = relation.get("attributes")     
        if attrs is None:
            attrs = []
            relation["attributes"] = attrs
        
        exists = any(a.get("name") == "riferimentoTestuale" for a in attrs)
        if not exists:
            attrs.append(new_attr)


    current_ontology_ident = json.dumps(new_json, indent=2, ensure_ascii=False)
    if current_ontology_ident is not None:
        print(f"Ontology '{text_filename}' created successfully!")
        ontology_file_name = f"Ontologies/{category}/{text_filename}_Ontology.json"
        with open(ontology_file_name, "w", encoding="utf-8") as file:
            file.write(current_ontology_ident)

    for i in range(index):
        file_path = Path(f"Ontologies/{category}/{text_filename}_{i}_Ontology.json")    
        if os.path.exists(file_path):
            os.remove(file_path)

        
def split_codice_appalti(file_path, ontology=False):
    """
    It splits the given text (of the "CodiceAppalti" category) into text chunks considering the "Articoli". 
    It returns the list of text chunks. 
    """
    pattern = re.compile(r'^Art\.\s*\d+(-[\w]+)?\.\s*\(.*\)$', flags=re.UNICODE)
    chunks = []
    chunk = ""
    with file_path.open('r', encoding='utf-8', errors='replace') as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.rstrip('\r\n')
            line_nfc = unicodedata.normalize('NFC', line)
            
            if pattern.match(line_nfc):
                if(len(chunk)>200 or ontology==False):
                    chunks.append(chunk)
                chunk = ""
            chunk = chunk + "\n" + line_nfc
        if(len(chunk)>100 or ontology==False):
            chunks.append(chunk)
    return chunks





def generate_ontology(category, model=None, dataItems=None):
    """
    It generates the ontology for each file of the given category (e.g. 'DiscplinaDiUtilizzo')
    """
    if dataItems is not None:
        all_text_paths = [item.text_path for item in dataItems]
    else:
        directory = Path(f"InputPDFtoText/{category}")
        all_text_paths = list(directory.rglob("*.txt"))
    
    if model is None:
        model = "openai/gpt-5-nano"

    print(f"{Fore.GREEN}--Generating the ontology for the '{category}' category")
    count = 1
    for text_path in all_text_paths:
        count = count + 1
        text_filename = text_path.name.removesuffix(".txt")
        #merge_ontologies_chunk(category, text_filename)

        if os.path.exists(text_path)==False:
            print(f"{Fore.RED} The .txt file '{text_path}' does not exist!")
            continue

        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read()

        print(f"{Fore.WHITE}File path: '{text_path}', current state: {count}/{len(all_text_paths)}.")

        file = Path(f"Ontologies/{category}/{text_filename}_Ontology.json")
        if file.exists():
           print(f"{Fore.WHITE}The ontology '{file}' already exists!")
           continue

        chunks = []
        if category == 'CodiceAppalti':
            chunks = split_codice_appalti(text_path, ontology=True)
        else:
            chunks = split_text_chunks(text)


        index_chunk = 0

        for chunk in chunks:
            chunk_path = Path(f"Ontologies/{category}/{text_filename}_{index_chunk}_Ontology.json")
            if chunk_path.exists():
                print(f"{Fore.WHITE}The chunk {chunk_path} has already been processed!")
                continue

            print(f"{Fore.WHITE}Processing chunk {index_chunk + 1}/{len(chunks)}.")
            textsToProcess = []
            textsToAdd = []
            textsToProcess.append(chunk)
            while(True):
                for text in textsToProcess: 
                    print(f"{Fore.WHITE}Waiting the LLM response...")
                    try:     
                        response = completion(
                            model=model,
                            messages=[
                                {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                                {"role": "user",   "content": Prompt.CREATE_ONTOLOGY_PROMPT_ITA.format(text=text)}
                            ]
                        )                  
                        process_response_ontology(text_filename, category, index_chunk, response, text, model)   
                    except ContextWindowExceededError as e:
                        mid = len(text) // 2
                        print(f"{Fore.WHITE}Halving the text size from '{len(text)}' to '{mid}'")
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
        merge_ontologies_chunk(category, text_filename)
        print(f"{Fore.GREEN}Ontology created for the '{category}' category!")



def process_reponse_data(text_filename, category, index_chunk, response, ontology, text, model):
    """
    It processes the LLM response generated by the data-extraction prompt. It returns the JSON object containing entities, relations and attributes.
    """
    response_content = response.choices[0].message["content"].strip()
    #response_content = unicodedata.normalize('NFD', response_content)
    #response_content =  ''.join(ch for ch in response_content if unicodedata.category(ch) != 'Mn')

    while(True):
        try:
            data = json.loads(extract_json(response_content))
            if "entities" not in data:
                raise Exception("Invalid data format. Missing entities")
            if "relations" not in data:
                raise Exception("Invalid data format. Missing relations")
            verify_json_data(data, ontology, category)
            break
        except Exception as e:
            try:
                # fallback 
                error = f"TypeError: '{type(e)}', error: '{e}'"
                print(f"Error extracting JSON. {error}")
                print(f"Prompting model to fix JSON")
                json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.EXTRACT_DATA_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_DATA_ITA.format(error=error, json=response_content, text=text)}         
                        ]
                    )
                response_content = json_fix_response.choices[0].message["content"].strip()
                #response_content = unicodedata.normalize('NFD',  response_content)
                #response_content =  ''.join(ch for ch in  response_content if unicodedata.category(ch) != 'Mn')
            except Exception as e:
                continue

   
    current_data_ident = json.dumps(data, indent=2, ensure_ascii=False)
    if current_data_ident is not None:
        print(f"{Fore.WHITE}Data '{text_filename} with index chunk:'{index_chunk}' created successfully!")
        data_file_name = f"JsonData/{category}/{text_filename}_{index_chunk}_Data.json"
        # Save the data to the disk as a json file.
        with open(data_file_name, "w", encoding="utf-8") as file:
            file.write(data_file_name)
        return data
    


def generate_data(category: str, model=None, dataItems=None):
    """
    Generate the Knowledge Graph for the given category (e.g. 'DiscplinaDiUtilizzo').
    """
    if dataItems is not None:
        all_text_paths = [item.text_path for item in dataItems]
    else:
        directory = Path(f"InputPDFtoText/{category}")
        all_text_paths = list(directory.rglob("*.txt"))
    
    if model is None:
        model = "openai/gpt-5-nano"
    json_category = [] # List containg all the JSON processed for the given category. Each JSON contains the entities, the relations and the attributes of a specific chunk of a specific .txt file. 
    count = 1

    print(f"{Fore.GREEN}--Generating the ontology for the '{category}' category")
    for text_path in all_text_paths:
        count = count + 1
        if os.path.exists(text_path)==False:
            print(f"{Fore.RED} The .txt file '{text_path}' does not exist!")
            continue

        print(f"{Fore.WHITE}File path: '{text_path}', current state: {count}/{len( all_text_paths)}.")

        text_filename = text_path.name
        text_filename = text_filename.removesuffix(".txt")
        ontology_file = f"Ontologies/{category}/{text_filename}_Ontology.json"
        if os.path.exists(ontology_file)==False:
            print(f"{Fore.RED}The ontology file '{ontology_file}' is missing!")
            continue

        with open(ontology_file, "r", encoding="utf-8") as file:
            textOntology = file.read()
        try:
            jsonOntology = json.loads(textOntology)
            ontology = Ontology.from_json(jsonOntology)
        except Exception as e:
            print(f"{Fore.RED}Failed to read the ontology {ontology_file}, error: {e}")
            continue

        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read()

        chunks = []
        if category == 'CodiceAppalti':
            chunks = split_codice_appalti(text)
        else:
            chunks = split_text_chunks(text)

        index_chunk = 0
        for chunk in chunks:
            print(f"{Fore.WHITE}Processing chunk {index_chunk + 1}/{len(chunks)}.")
            textsToProcess = []
            textsToAdd = []
            textsToProcess.append(chunk)

            while(True):
                for text in textsToProcess: 
                    print(f"{Fore.WHITE}Waiting the LLM response...")
                    try:
                        response = completion(
                            model=model,
                            messages=[
                                {"role": "system", "content": Prompt.EXTRACT_DATA_SYSTEM_ITA},
                                {"role": "user",   "content": Prompt.EXTRACT_DATA_PROMPT_ITA.format(ontology=ontology, text=chunk)}
                            ]
                        )
                        json_data_chunk = process_reponse_data(text_filename, category, index_chunk, response, ontology, chunk, model)
                        if json_data_chunk is not None:
                            json_category.append(json_data_chunk)
                    except ContextWindowExceededError as e:
                        mid = len(text) // 2
                        print(f"{Fore.WHITE}Halving the text size from '{len(text)}' to '{mid}'")
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
    aggregate_Json = aggregate_data(json_data_chunk)
    json_refined = refine_with_LLM(aggregate_Json, category, ontology)
    upload_correctly=upload_data(category, json_refined, ontology)
    if upload_correctly:
        print(f"{Fore.GREEN}Upload completed successfully for the '{category}' category!")
    else:
        print(f"{Fore.RED}Upload completed with ERRORS for the '{category}' category!")





def agglomerative_clustering(item_embedding_dict: dict, distance_threshold=0.5):
    """
    It performs the agglomerative clustering operation on the given items.
    
    :param item_embedding_dict: dictionary where each key is a generic item and the value si the corresponding embedding vector. 
    :param distance_threshold: the linkage distance threshold at or above which clusters will not be merged.
    """
    items = list(item_embedding_dict.keys())
    # Convert the embeddings in a numpy matrix
    X = np.array(list(item_embedding_dict.values()))
    # Distance range values [0,2]
    clustering = AgglomerativeClustering(n_clusters=None, metric="cosine", compute_full_tree=True, linkage="maximum", distance_threshold=distance_threshold)
    labels = clustering.fit_predict(X)

    clusters = dict()
    for item, label_cluster in zip(items, labels):
        clusters[label_cluster].append(item)

    return clusters

def ask_LLM_merge_similar_relations(similar_relations, category, ontology, model=None):
    """
    Given a list of relations, it prompts the LLM to merge any duplicates.
    """
    if model is None:
        model = "openai/gpt-5-nano"

    print(f"{Fore.WHITE}Asking LLM to merge similar relations..")
    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": Prompt.MERGE_SIMILAR_ENTITIES_SYSTEM_ITA},
            {"role": "user",   "content": Prompt.MERGE_SIMILAR_ENTITIES_PROMPT_ITA.format(relations=similar_relations)}
        ]
    )
    response_content = response.choices[0].message["content"].strip()
    while(True):
        try:
            new_relations = json.loads(extract_json(response_content))
            if "relations" not in new_relations:
                raise Exception(f"{Fore.WHITE}Invalid data format. Missing entities")
            verify_json_data(new_relations, category, ontology)
            print(f"{Fore.WHITE}Relations merged correctly!")
            break
        except Exception as e:
            try:
                # fallback 
                error = f"TypeError: '{type(e)}', error: '{e}'"
                print(f"Error extracting JSON. {error}")
                json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.MERGE_SIMILAR_ENTITIES_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.MERGE_SIMILAR_ENTITIES_PROMPT_ITA.format(relations=similar_relations)}         
                        ]
                    )
                response_content = json_fix_response.choices[0].message["content"].strip()
            except Exception as e:
                continue
    return new_relations



def ask_LLM_merge_similar_entities(similar_entities, category, ontology, model=None):
    """
    Given a list of entities, it prompts the LLM to merge any duplicates.
    """
    if model is None:
        model = "openai/gpt-5-nano"

    print(f"{Fore.WHITE}Asking LLM to merge similar entities..")
    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": Prompt.MERGE_SIMILAR_ENTITIES_SYSTEM_ITA},
            {"role": "user",   "content": Prompt.MERGE_SIMILAR_ENTITIES_PROMPT_ITA.format(entities=similar_entities)}
        ]
    )
    response_content = response.choices[0].message["content"].strip()
    while(True):
        try:
            new_entities = json.loads(extract_json(response_content))
            if "entities" not in new_entities:
                raise Exception("Invalid data format. Missing entities")
            verify_json_data(new_entities, ontology, category)
            print(f"{Fore.WHITE}Entities merged correctly!")
            break
        except Exception as e:
            try:
                # fallback 
                error = f"{Fore.WHITE}TypeError: '{type(e)}', error: '{e}'"
                print(f"{Fore.WHITE}Error extracting JSON. {error}")
                json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.MERGE_SIMILAR_ENTITIES_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.MERGE_SIMILAR_ENTITIES_PROMPT_ITA.format(entities=similar_entities)}       
                        ]
                    )
                response_content = json_fix_response.choices[0].message["content"].strip()
            except Exception as e:
                continue
    return new_entities


def refine_with_LLM(json_data, category, ontology):
    """
    It takes as input a JSON object containing entities, relations, and attributes. Using the embeddings, it finds similar nodes or edges that can be duplicates.
    The duplicates are removed and merged using LLM.
    """
    # We define a "text description" as an artificial text constructed to describe a relation or an entity.
    by_text_desciption_entity_dict = dict() # The key is the entity and the value is the 'text_description'
    by_text_desciption_relation_dict = dict() # The key is the relation and the value is the 'text_description'

    for entity in json_data["entities"]:
        text_descritpion = ""
        label = entity.get("label")
        text_descritpion = f"label:'{label}'"
        attrs = entity.get("attrs")
        if attrs is not None:  
            for key, value in attrs.items():
                text_descritpion = text_descritpion + f", '{key}':'{value}'"
        text_descritpion = text_descritpion + "."
        by_text_desciption_entity_dict[entity].append(text_descritpion)
        

    for relation in json_data["relations"]:
        text_descritpion = ""
        label = relation.get("label")
        text_descritpion = f"label:'{label}'"
        source = relation.get("source")
        source_label = source.get("label")
        text_descritpion = text_descritpion + f", sourceLabel:'{source_label}'"
        source_attrs = source.get("attributes")
        if source_attrs is not None:
            for key, value in source_attrs.items():
                text_descritpion = text_descritpion + f", sourceAttribute_{key}:'{value}'"
        target = relation.get("target")
        target_label = target.get("label")
        text_descritpion = text_descritpion + f", targetLabel:'{target_label}'"
        target_attrs = target.get("attributes")
        if target_attrs is not None:
            for key, value in target_attrs.items():
                text_descritpion = text_descritpion + f", targetAttribute_{key}:'{value}"
        relation_attrs = relation.get("attrs")
        if relation_attrs is not None:
            for key, value in relation_attrs.items():
                text_descritpion = text_descritpion + f", relationAttribute_{key}:'{value}'"
        text_descritpion = text_descritpion + "." 
        by_text_desciption_relation_dict[relation].append(text_descritpion)
       

    by_text_description_embedding_entity_dict = dict() # The key is the entity and the value is the embedding of the 'text description'
    for entity, text_descritpion in by_text_desciption_entity_dict():         
        response_embedding = embedding(
            model="text-embedding-3-small",
            input=text_descritpion
        )
        embedding = response_embedding["data"][0]["embedding"]
        by_text_description_embedding_entity_dict[entity] = embedding
    entity_partitions = agglomerative_clustering(by_text_description_embedding_entity_dict)


    by_text_description_embedding_relation_dict = dict() # The key is the relarion and the value is the embedding of the 'text description'
    for relation, text_descritpion in by_text_desciption_relation_dict():         
        response_embedding = embedding(
            model="text-embedding-3-small",
            input=text_descritpion
        )
        embedding = response_embedding["data"][0]["embedding"]
        by_text_description_embedding_relation_dict[relation] = embedding
    relation_partisions = agglomerative_clustering(by_text_description_embedding_relation_dict)

    for cluster_id, entities_in_partition in entity_partitions.itmes():
        new_entities = ask_LLM_merge_similar_entities(entities_in_partition.copy(), category, ontology)
        ids_to_remove = {id(e) for e in entities_in_partition}
        json_data['entities'] = [in_json for in_json in json_data['entities'] if id(in_json) not in ids_to_remove]
        json_data['entities'].extend(new_entities)


    for cluster_id, relations_in_partition in relation_partisions.itmes():
        new_relations = ask_LLM_merge_similar_relations(relations_in_partition.copy())
        ids_to_remove = {id(e) for e in relations_in_partition}
        json_data['relations'] = [in_json for in_json in json_data['relations'] if id(in_json) not in ids_to_remove]
        json_data['relations'].extend(new_relations)





def aggregate_data(json_data_list : list):
    """
    Takes as input a list of JSON objects containing entities, relations, and attributes, and merges them into a single JSON object.
    It also merges entities and relations that have identical labels and attributes, which is useful for removing duplicates in this regard. 
    """
    json_data = [item for sublist in json_data_list for item in sublist] # flatten
    
    by_ID_entity_dict = dict() # The key is an artificial entity identifier that contains the label of the entity and a concatenated string of the values of all attributes. The value is the list of entities with that identifier. This is useful for finding duplicate entities.
    by_ID_relation_dict = dict() # The key is an artificial relation identifier that contains the label of the relation and a concatenated string of the values of all source and target attributes. The value is the list of relations with that identifier. This is useful for finding duplicate relations.

    for entity in json_data["entities"]:
        artificial_entity_identifier = ""
        label = entity.get("label")
        artificial_entity_identifier = f"label:{label}"
        attrs = entity.get("attributes")
        if attrs is not None:
            for key, value in attrs.items():
                if key!="riferimentoTestuale":
                    artificial_entity_identifier = artificial_entity_identifier + f".{key}:{value}"
        by_ID_entity_dict[artificial_entity_identifier.lower()].append(entity)

    for relation in json_data["relations"]:
        artificial_relation_identifier = ""
        label = relation.get("label") 
        artificial_relation_identifier = f"label:{label}"
        source = relation.get("source")
        source_label = source.get("label")
        artificial_relation_identifier = artificial_relation_identifier + f".sourceLabel:{source_label}"
        source_attributes = source.get("attributes")
        target = relation.get("target")
        target_label = target.get("label")
        artificial_relation_identifier = artificial_relation_identifier + f".targetLabel:{target_label}"
        target_attributes = target.get("attributes")
        if source_attributes is not None:
            for key, value in source_attributes.items():
                if key!="riferimentoTestuale":
                    artificial_relation_identifier = artificial_relation_identifier + f".sourceAttr{key}:{value}"
        if target_attributes is not None:
            for key, value in target_attributes.items():
                if key!="riferimentoTestuale":
                    artificial_relation_identifier = artificial_relation_identifier + f".targetAttr{key}:{value}"
        by_ID_relation_dict[artificial_relation_identifier.lower()].append(relation)

    
    filtered_entities_dict = {
        artificial_entity_identifier: entities
        for artificial_entity_identifier, entities in by_ID_entity_dict.items()
        if len(entities) >= 2
    }

        
    filtered_relations_dict = {
        artificial_relation_identifier: relations
        for artificial_relation_identifier, relations in by_ID_relation_dict.items()
        if len(relations) >= 2
    }


    for artificial_entity_identifier, entities in filtered_entities_dict.items():
        new_entities = ask_LLM_merge_similar_entities(entities.copy())
        ids_to_remove = {id(e) for e in entities}
        json_data['entities'] = [in_json for in_json in json_data['entities'] if id(in_json) not in ids_to_remove]
        json_data['entities'].extend(new_entities)

    for artificial_relation_identifier, relations in filtered_relations_dict.items():
        new_relations = ask_LLM_merge_similar_relations(relations.copy())
        ids_to_remove = {id(e) for e in relations}
        json_data['relations'] = [in_json for in_json in json_data['relations'] if id(in_json) not in ids_to_remove]
        json_data['relations'].extend(new_relations)

    return json_data


def verify_json_data(jsonData, ontology, category):
    """
    Veirifies that the input JSON object conforms to the required structure for entity and relation extraction. 
    Returns the string 'True' on success; raises an exception on failure.
    """
    client = FalkorDB(**client_kwargs)
    graph = client.select_graph(category)
    for entity in jsonData["entities"]:
        try:
            entity = ontology.get_entity_with_label(jsonData["label"])
            if entity is None:
                raise Exception(f"Entity with label {jsonData['label']} not found in ontology")
            unique_attributes_schema = [attr for attr in entity.attributes if attr.unique]
            unique_attributes = {
                attr.name: (
                    jsonData["attributes"][attr.name] if attr.name in jsonData["attributes"] else ""
                )
                for attr in unique_attributes_schema
            }
            unique_attributes_text = map_dict_to_cypher_properties(unique_attributes)
            non_unique_attributes = {
                attr.name: jsonData["attributes"][attr.name]
                for attr in entity.attributes
                if not attr.unique and attr.name in jsonData["attributes"]
            }
            non_unique_attributes_text = map_dict_to_cypher_properties(
                non_unique_attributes
            )
        except Exception as e:
            if "label" in entity:
                error = f"{Fore.RED}Error while validating the entity '{entity.get("label")}', error '{e}'."
            else:
                error = f"{Fore.RED}Error while validating an entity, error '{e}'."
            raise Exception(error)

    for relation in jsonData["relations"]:
        try:
            relations = ontology.get_relations_with_label(jsonData["label"])
            if len(relations) == 0:
                raise Exception (f"Relations with label {jsonData['label']} not found in ontology")
            source_unique_attributes = (
                jsonData["source"]["attributes"]
                if "source" in jsonData and "attributes" in jsonData["source"]
                else {}
            )
            source_unique_attributes_text = map_dict_to_cypher_properties(
                source_unique_attributes
            )

            target_unique_attributes = (
                jsonData["target"]["attributes"]
                if "target" in jsonData and "attributes" in jsonData["target"]
                else {}
            )
            target_unique_attributes_text = map_dict_to_cypher_properties(
                target_unique_attributes
            )

            relation_attributes = (
                map_dict_to_cypher_properties(jsonData["attributes"])
                if "attributes" in jsonData
                else {}
            )
        except Exception as e:
            if "label" in relation:
                error = f"{Fore.RED}Error while validating the relation '{relation.get("label")}', error '{e}'."
            else:
                error = f"{Fore.RED}Error while validating a relation, error '{e}'."
            raise Exception(error)
    return True


def upload_data(category, jsonData, ontology, model=None):
    """
    It uploads the given data to FalkorDB. It returns 'True' on success; 'False' on failure.
    """
    client = FalkorDB(**client_kwargs)
    graph = client.select_graph(category)
    if model is None:
        model = "openai/gpt-5-nano"
    
    print(f"{Fore.WHITE}Uploading the data...")
    hasErrors=False
        
    for entity in jsonData["entities"]:
        try:
            extract_data_step.create_entity(graph, entity, ontology)
        except Exception as e:
            print(f"{Fore.RED}Error creating entity: {e}")
            hasErrors=True
            continue

    for relation in jsonData["relations"]:
        try:
            extract_data_step.create_relation(graph, relation, ontology)
        except Exception as e:
            print(f"{Fore.RED}Error creating relation: {e}")
            hasErrors=True
            continue

    if hasErrors==False:
        return True
    else:
        return False




def create_dir():
    os.makedirs("Ontologies",  exist_ok=True)    
    os.makedirs("Ontologies/DisciplinaDiUtilizzo",  exist_ok=True)    
    os.makedirs("Ontologies/Normativa",  exist_ok=True)    
    os.makedirs("Ontologies/FAQ",  exist_ok=True)    
    os.makedirs("Ontologies/GuidePratiche",  exist_ok=True) 
    os.makedirs("Ontologies/CodiceAppalti",  exist_ok=True) 


    os.makedirs("JsonData",  exist_ok=True)    
    os.makedirs("JsonData/DisciplinaDiUtilizzo",  exist_ok=True)    
    os.makedirs("JsonData/Normativa",  exist_ok=True)    
    os.makedirs("JsonData/FAQ",  exist_ok=True)    
    os.makedirs("JsonData/GuidePratiche",  exist_ok=True) 
    os.makedirs("JsonData/CodiceAppalti",  exist_ok=True) 

    os.makedirs("InputPDFtoText",  exist_ok=True)   
    os.makedirs("InputPDFtoText/DisciplinaDiUtilizzo",  exist_ok=True)    
    os.makedirs("InputPDFtoText/Normativa",  exist_ok=True)    
    os.makedirs("InputPDFtoText/FAQ",  exist_ok=True)    
    os.makedirs("InputPDFtoText/GuidePratiche",  exist_ok=True) 
    os.makedirs("InputPDFtoText/CodiceAppalti",  exist_ok=True) 




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
        print(f"{Fore.WHITE}---------------------------")
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
                print("5. CodiceAppalti")
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
                elif choice_cat == 5:
                    generate_ontology("CodiceAppalti")
                    break
                else:
                    print("Invalid choice. Please try again")
        elif choice == 3:
            while(True):
                print("1. DisciplinaDiUtilizzo")
                print("2. GuidePratiche")
                print("3. Normativa")
                print("4. FAQ")
                print("5. CodiceAppalti")
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
                elif choice_cat == 5:
                    generate_data("CodiceAppalti")
                    break
                else:
                    print("Invalid choice. Please try again")
        elif choice == 4:
            break
        else:
            print("Invalid choice. Please try again")
        


if __name__ == "__main__":
    main()

