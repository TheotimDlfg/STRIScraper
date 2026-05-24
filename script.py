import requests
from bs4 import BeautifulSoup
import re
import os
import zipfile
from datetime import datetime
import getpass

class MoodleScraper:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        self.classifiers = {
            'TD': re.compile(r'\bTD[\s\-_]*\d*\b', re.IGNORECASE),
            'TP': re.compile(r'\bTP[\s\-_]*\d*\b', re.IGNORECASE),
            'Annales': re.compile(r'\b(contr[ôo]le|annale|examen|partiel)\b', re.IGNORECASE),
            'Cours': re.compile(r'\b(cours|diapositive|s[é|e]quence|chapitre|pr[é|e]sentation|part(?:ie)?[\s\-_]*\d*)\b', re.IGNORECASE)
        }

    def authenticate(self, username, password):
        """Initialise la persistance de session HTTP."""
        login_url = f"{self.base_url}/login/index.php"
        res = self.session.get(login_url)
        res.raise_for_status()
        
        soup = BeautifulSoup(res.text, 'lxml')
        token_input = soup.find('input', {'name': 'logintoken'})
        if not token_input:
            raise ValueError("Erreur I/O: logintoken introuvable. Altération du DOM détectée.")
            
        payload = {
            'username': username,
            'password': password,
            'logintoken': token_input.get('value')
        }
        
        auth_res = self.session.post(login_url, data=payload)
        auth_res.raise_for_status()
        
        if 'sesskey' not in auth_res.text and 'testsession' in auth_res.url:
             raise PermissionError("Échec de l'authentification : I/O bloqué.")

    def _sanitize_filename(self, filename):
        """Standardisation des chaînes pour l'arborescence (FileSystem virtuel)."""
        return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

    def _classify_resource(self, filename):
        """Classification heuristique par expressions régulières (Regex)."""
        for category, regex in self.classifiers.items():
            if regex.search(filename):
                return category
        return "Autres"

    def get_all_courses(self):
        """Parsing DOM du Dashboard pour extraction dynamique des IDs de cours."""
        dashboard_url = f"{self.base_url}/my/index.php"
        res = self.session.get(dashboard_url)
        res.raise_for_status()
        
        soup = BeautifulSoup(res.text, 'lxml')
        courses = {}
        
        course_nodes = soup.find_all('a', href=re.compile(r'course/view\.php\?id=\d+'))
        for node in course_nodes:
            match = re.search(r'id=(\d+)', node['href'])
            if match:
                course_id = match.group(1)
                course_name = self._sanitize_filename(node.text)
                if course_name and course_id not in courses:
                    courses[course_id] = course_name
                    
        return courses

    def _download_to_zip(self, download_url, raw_name, course_name, zip_archive, written_paths, date_suffix):
        """Pipeline d'I/O binaire avec gestion des collisions spatio-temporelles."""
        safe_full = self._sanitize_filename(raw_name)
        name_part, ext_part = os.path.splitext(safe_full)
        
        category = self._classify_resource(name_part)
        
        doc_res = self.session.get(download_url, stream=True)
        if doc_res.status_code == 200:
            if not ext_part:
                content_disposition = doc_res.headers.get('content-disposition', '')
                ext_part = ".pdf"
                if 'filename=' in content_disposition:
                    extracted_ext = os.path.splitext(content_disposition.split('filename=')[-1].strip('"\''))[1]
                    if extracted_ext:
                        ext_part = extracted_ext
                        
            base_path = f"{course_name}/{category}/{name_part}"
            archive_path = f"{base_path}{ext_part}"
            
            # Mécanisme de résolution itérative de collision
            if archive_path in written_paths:
                archive_path = f"{base_path}_{date_suffix}{ext_part}"
                counter = 1
                while archive_path in written_paths:
                    archive_path = f"{base_path}_{date_suffix}_{counter}{ext_part}"
                    counter += 1
                    
            written_paths.add(archive_path)
            zip_archive.writestr(archive_path, doc_res.content)
            print(f"    [+] {archive_path}")
        else:
            print(f"    [!] Erreur HTTP {doc_res.status_code} sur : {name_part}")

    def global_extract_and_archive(self, output_zip_path):
        """Architecture principale du Crawler (Binaires + Liens URL)."""
        courses = self.get_all_courses()
        if not courses:
            print("Alerte: Aucun cours détecté sur le Dashboard.")
            return

        print(f"Extraction globale initialisée. {len(courses)} cours détectés.")
        date_suffix = datetime.now().strftime("%d-%m-%y")

        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_archive:
            written_paths = set()
            
            for course_id, course_name in courses.items():
                print(f"-> Traitement du cours : {course_name} (ID: {course_id})")
                course_url = f"{self.base_url}/course/view.php?id={course_id}"
                
                res = self.session.get(course_url)
                if res.status_code != 200:
                    continue
                    
                soup = BeautifulSoup(res.text, 'lxml')
                
                # Buffer volatil pour le Vecteur 3
                course_url_links = []
                
                # VECTEUR 1 : Ressources standards (mod/resource)
                for link in soup.find_all('a', href=re.compile(r'mod/resource/view\.php')):
                    name_node = link.find('span', class_='instancename')
                    if not name_node: continue
                        
                    accesshide_node = name_node.find('span', class_='accesshide')
                    if accesshide_node: accesshide_node.extract()
                        
                    download_url = link['href']
                    if 'redirect=1' not in download_url:
                        download_url += '&redirect=1'
                        
                    self._download_to_zip(download_url, name_node.text.strip(), course_name, zip_archive, written_paths, date_suffix)

                # VECTEUR 2 : Répertoires (mod/folder)
                for folder_link in soup.find_all('a', href=re.compile(r'mod/folder/view\.php')):
                    folder_res = self.session.get(folder_link['href'])
                    if folder_res.status_code == 200:
                        folder_soup = BeautifulSoup(folder_res.text, 'lxml')
                        
                        for file_link in folder_soup.find_all('a', href=re.compile(r'pluginfile\.php')):
                            fp_node = file_link.find('span', class_='fp-filename')
                            if not fp_node: continue
                                
                            download_url = file_link['href']
                            if 'forcedownload=1' not in download_url:
                                separator = '&' if '?' in download_url else '?'
                                download_url += f'{separator}forcedownload=1'
                                
                            self._download_to_zip(download_url, fp_node.text.strip(), course_name, zip_archive, written_paths, date_suffix)

                # VECTEUR 3 : Liens externes suggérés (mod/url)
                for url_link in soup.find_all('a', href=re.compile(r'mod/url/view\.php')):
                    name_node = url_link.find('span', class_='instancename')
                    if not name_node: continue
                        
                    accesshide_node = name_node.find('span', class_='accesshide')
                    if accesshide_node: accesshide_node.extract()
                        
                    link_name = name_node.text.strip()
                    actual_url = url_link['href']
                    course_url_links.append(f"- {link_name} : {actual_url}")
                
                # I/O Flush : Génération du fichier d'index des URLs
                if course_url_links:
                    txt_path = f"{course_name}/Ressources_suggerees.txt"
                    links_content = "=== Liens et Ressources suggérés ===\n\n" + "\n".join(course_url_links)
                    zip_archive.writestr(txt_path, links_content.encode('utf-8'))
                    print(f"    [+] {txt_path} ({len(course_url_links)} entrées indexées)")

# ==========================================
# Exécution
# ==========================================
if __name__ == "__main__":
    BASE_URL = "https://www.stri.fr/eformation"
    
    USERNAME = input("Identifiant: ")
    PASSWORD = getpass.getpass("Mot de passe: ")
    
    scraper = MoodleScraper(BASE_URL)
    
    try:
        scraper.authenticate(USERNAME, PASSWORD)
        print("Authentification : Succès.")
        
        zip_path = "Archives_Globales_Moodle.zip"
        scraper.global_extract_and_archive(zip_path)
        print(f"Extraction terminée. Fichier généré : {zip_path}")
        
    except Exception as e:
        print(f"Erreur critique lors de l'exécution : {e}")