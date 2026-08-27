FROM python:3.13-slim
 
# Librairies systeme necessaires a OpenCV/rapidocr, meme en version headless
# (rapidocr tire une dependance transitive qui en a besoin quand meme).
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
 
WORKDIR /app
 
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
COPY . .
 
# Enchaine synchronisation TCGdex + construction de l'index + demarrage du
# serveur. Tourne a chaque redemarrage tant qu'aucun volume persistant n'est
# configure (a ajouter plus tard pour eviter de tout refaire a chaque fois).
CMD ["sh", "-c", "python main.py sync && python main.py index build && python main.py serve --host 0.0.0.0 --port $PORT"]
