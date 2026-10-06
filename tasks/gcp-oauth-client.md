Google Cloud project: <PROJECT_ID>   (Google account: the one already signed in)

Goal: create an OAuth client for a desktop app, so a local script can upload to YouTube.

1. Open https://console.cloud.google.com/auth/overview?project=<PROJECT_ID>.
   If the Google Auth Platform is not configured yet, click "Get started" and fill in:
   app name "<APP_NAME>", support email = the signed-in account, audience "External",
   contact email = the signed-in account. Agree to the policy and create.
2. Under Audience, add <TEST_USER_EMAIL> as a test user (skip if already there).
3. Under Clients, create a client: application type "Desktop app", name "<CLIENT_NAME>".
4. Do NOT download or display the client secret. Just report the client ID and say it was created.
