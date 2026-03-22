import askLLM
import GenerateKG
import random
from litellm import completion, ContextWindowExceededError
import Prompt
import csv
import os
from pathlib import Path
from datetime import datetime
from colorama import init, Fore, Style
from tavily import TavilyClient
from dotenv import load_dotenv
from enum import Enum


# Interaction type using LLM
class InteractionType(Enum):
    WEB = "USING WEB DATA"
    ONLY_TRAIN = "USING ONLY TRAIN DATA"
    WITH_KG = "USING THE KG"


load_dotenv()

tavyl_api_key = os.getenv("TAVILY_API_KEY")
client = TavilyClient(api_key=tavyl_api_key)

def ask_with_web(question, question_complete, model_name):
    to_search = "Nel contesto della piattaforma EmPulia e del Codice degli appalti italiano..." + question
    search = client.search(query=to_search, search_depth="basic")

    results = "\n".join([r["content"] for r in search["results"][:5]])

    # 2. passa i risultati al modello
    response = completion(
        model=model_name,
         messages=[
                {"role": "system", "content": Prompt.ASK_GENERIC_QUESTION_EMPULIA_SYSTEM_ITA},
                {"role": "user",   "content": Prompt.ASK_GENERIC_QUESTION_EMPULIA_WEB_PROMPT_ITA.format(text=question_complete, info_web=results)}         
               ], 
    )

    return response["choices"][0]["message"]["content"]


def main():
    number_of_tests = int(input("Choose the number of tests: "))
    correct_answers_with_kg = 0
    correct_answers_only_train = 0
    correct_answers_with_web = 0
    total_answers = 0
    #categories = ["DisciplinaDiUtilizzo", "CodiceAppalti", "FAQ", "GuidePraticheOE", "GuidePraticheSA"]
    categories = ["GuidePraticheSA"]
    model = "openai/gpt-5-nano"


    for i in range(number_of_tests):
        category = random.choice(categories)
        print(f"{Fore.GREEN} ---- Category: '{category}'")
        directory = Path(f"InputPDFtoText/{category}")
        all_text_paths = list(directory.rglob("*.txt"))
        text_path = random.choice(all_text_paths) # We choose randomly a .txt file of any category
        print(f"Text path: '{text_path}'")
        with open(text_path, "r", encoding="utf-8") as file:
            text = file.read()
        if text_path == "InputPDFtoText/CodiceAppati/main.txt":
            chunks = GenerateKG.split_codice_appalti(text_path)
        else:
            chunks = GenerateKG.split_text_chunks(text, False)
        chunk = random.choice(chunks)
        print("Constructing the question...")
        question_to_ask = completion(
                            model=model,
                            messages=[
                                {"role": "system", "content": Prompt.LLM_AS_JUDGE_SYSTEM},
                                {"role": "user",   "content": Prompt.LLM_AS_JUDGE_QUESTION_TO_ASK.format(text=chunk)}         
                            ]
                        )
        question = question_to_ask.choices[0].message.content
        question_complete = question + " Fornisci una sola risposta diretta, non rispondere per nessuna ragione con altre domande!"

        
        total_answers += 1
        for type_interation in InteractionType:

            if type_interation == InteractionType.WITH_KG:
                results_filename = "ResultsWithKGs.csv"
            elif type_interation == InteractionType.ONLY_TRAIN:
                results_filename = "ResultsOnlyTrain.csv"
            elif type_interation == InteractionType.WEB:
                results_filename = "ResultsWithWEB.csv"


            if type_interation == InteractionType.WITH_KG:
                print("Asking the question (LLM with KG)...")
                _, answer = askLLM.ask(question_complete, None)
                if answer is None:
                    continue
            elif type_interation == InteractionType.ONLY_TRAIN:
                print("Asking the question (LLM only train)...")
                answer_completion = completion(
                            model=model,
                            messages=[
                                    {"role": "system", "content": Prompt.ASK_GENERIC_QUESTION_EMPULIA_SYSTEM_ITA},
                                    {"role": "user",   "content": Prompt.ASK_GENERIC_QUESTION_EMPULIA_PROMPT_ITA.format(text=question_complete)}         
                                ],                          
                        )
                answer = answer_completion.choices[0].message.content

            elif type_interation == InteractionType.WEB:
                print("Asking the question (LLM with WEB)...")
                answer = ask_with_web(question, question_complete, model)

      
            print("Constructing the verdict...")
            judge_response = completion(
                model=model,
                messages=[
                    {"role": "system", "content": Prompt.LLM_AS_JUDGE_SYSTEM},
                    {"role": "user", "content": Prompt.LLM_AS_JUDGE_EVALUATION.format(
                        text=chunk, 
                        question=question, 
                        answer=answer
                    )}
                ]
            )
            
            verdict = judge_response.choices[0].message.content.strip().lower()


            with open(results_filename, "a", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                if not results_filename or os.stat(results_filename).st_size == 0:
                    writer.writerow(["Interaction type", "Timestamp", "Category", "Question", "Answer", "Verdict"])
                new_data = [str(type_interation), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), category, question.strip(), answer.strip()]
                if "sì" in verdict or "yes" in verdict or "si" in verdict:
                    if type_interation == InteractionType.WITH_KG:
                        correct_answers_with_kg += 1
                    elif type_interation == InteractionType.ONLY_TRAIN:
                        correct_answers_only_train += 1
                    elif type_interation == InteractionType.WEB:
                        correct_answers_with_web +=1

                    print(f"Test {i+1}: ✅ Correct")
                    new_data.append("1")
                else:
                    print(f"Test {i+1}: ❌ Wrong")
                    new_data.append("0")
                writer.writerow(new_data)                                     


    print(f"\n--- Final results ---")
    accuracy_with_kg = (correct_answers_with_kg / total_answers) * 100
    print(f"Total accuracy with KG: {accuracy_with_kg:.2f}% ({correct_answers_with_kg}/{total_answers})")

    accuracy_only_train = (correct_answers_only_train / total_answers) * 100
    print(f"Total accuracy only train: {accuracy_only_train:.2f}% ({correct_answers_only_train}/{total_answers})")

    accuracy_with_web = (correct_answers_with_web / total_answers) * 100
    print(f"Total accuracy with web: {accuracy_with_web:.2f}% ({correct_answers_with_web}/{total_answers})")


        

if __name__ == "__main__":
    main()
