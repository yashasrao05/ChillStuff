1. Create a virtual environment:

   ```bash
   python -m venv venv
   cd mcp-starter
   .venv\Scripts\activate

2. Install Dependencies:

    ```bash
    pip install -r requirements.txt

3. Update dependencies:

    ```bash
    pip freeze > requirements.txt

4. Deactivate venv

    ```bash
    deactivate


5. ngrok public url
https://<id>.ngrok-free.app

6. To start mcp server and ngrok
cd .\mcp-bearer-token\
python mcp_starter.py
ngrok http 8086

