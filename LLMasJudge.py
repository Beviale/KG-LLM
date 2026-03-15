import askLLM
import GenerateKG
import random
from litellm import completion, ContextWindowExceededError
import Prompt
import csv
import os
from pathlib import Path



def main():
    number_of_tests = int(input("Choose the number of tests: "))
    correct_answers = 0
    total_answers = 0
    categories = ["DisciplinaDiUtilizzo", "CodiceAppalti", "FAQ", "GuidePraticheOE"]
    model = "openai/gpt-5-nano"
    orchestrator = None
    results_filename = "Results.csv"

    for i in range(number_of_tests):
        category = random.choice(categories)
        directory = Path(f"InputPDFtoText/{category}")
        all_text_paths = list(directory.rglob("*.txt"))
        text_path = random.choice(all_text_paths) # We choose randomly a .txt file of any category
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
        question = question + " Fornisci una sola risposta diretta, non rispondere con altre domande!"
        print("Asking the question...")
        orchestrator, answer = askLLM.ask(question, orchestrator)
        if answer is None:
            continue
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
        total_answers += 1

        with open(results_filename, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if not results_filename or os.stat(results_filename).st_size == 0:
                writer.writerow(["Category", "Question", "Answer", "Verdict"])
            new_data = [category, question.strip(), answer.strip()]
            if "sì" in verdict or "yes" in verdict or "si" in verdict:
                correct_answers += 1
                print(f"Test {i+1}: ✅ Correct")
                new_data.append("1")
            else:
                print(f"Test {i+1}: ❌ Wrong")
                new_data.append("0")
            writer.writerow(new_data)

    accuracy = (correct_answers / total_answers) * 100
    print(f"\n--- Final results ---")
    print(f"Total accuracy: {accuracy:.2f}% ({correct_answers}/{total_answers})")

        

if __name__ == "__main__":
    main()