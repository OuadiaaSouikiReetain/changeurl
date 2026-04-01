# ChangeURL App

  ## Clone
  ```powershell
  git clone https://github.com/OuadiaaSouikiReetain/changeurl.git
  cd changeurl

  ## Create virtual environment

  python -m venv venv
  .\venv\Scripts\Activate.ps1

  ## Install dependencies

  pip install -r .\sfmc-url-modifier-ui\requirements.txt

  ## Configure environment

  Create this file:

  sfmc-url-modifier-ui\.env

  You can copy from:

  sfmc-url-modifier-ui\.env.example

  Example:

  SFMC_CLIENT_ID=your_client_id
  SFMC_CLIENT_SECRET=your_client_secret
  SFMC_SUBDOMAIN=your_subdomain
  SFMC_MID=your_mid

  ## Run the app

  cd .\sfmc-url-modifier-ui
  python app.py

  Open in browser:

  http://localhost:5001

  ## Notes

  - .env is not included in the repository, you must create it locally.
  - Keep the repository structure unchanged after clone.
  - The UI uses code from:
      - sfmc-url-modifier
      - sfmc-welcome-url-modifier
