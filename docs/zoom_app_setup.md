# Zoom App Setup Guide

To receive webhooks when a guest joins a Zoom meeting (which triggers the voice agent to join), you need an Internal Zoom App.

## 1. Create a Server-to-Server OAuth App

1. Log in to your Zoom account.
2. Go to the [Zoom App Marketplace](https://marketplace.zoom.us/).
3. Click **Develop** in the top right corner and select **Build App**.
4. Choose **Server-to-Server OAuth** and click **Create**.
5. Give your app a name (e.g., "Vedic Pathway Agent").

## 2. Configure the App

### Information
Fill in the required information (Company Name, Developer Contact).

### Scopes
You need to add scopes so your app can receive webhooks.

1. Click **+ Add Scopes**.
2. Go to **Meeting**.
3. Check `meeting:read:admin` (Allows you to view meeting details).
4. Click **Done**.

### Feature (Webhooks)
This is where you tell Zoom where to send events.

1. Toggle the **Event Subscriptions** switch to ON.
2. Click **+ Add Event Subscription**.
3. **Subscription Name:** e.g., "Participant Joined Subscription"
4. **Event notification endpoint URL:** `https://attendee.vedicpathway.com/webhooks/zoom`
5. Under **Events**, click **+ Add Events**.
6. Go to **Meeting**, and check:
   - `meeting.participant_joined`
   - `meeting.participant_left` (Optional)
7. Click **Done** and **Save**.

## 3. Retrieve Credentials

Go to the **App Credentials** and **Feature** tabs to get the required information for your `.env` file:

1. In the **Feature** tab, under your Event Subscription, find the **Secret Token**.
2. Copy this value.
3. Open your project's `.env` file and add it:
   ```env
   ZOOM_WEBHOOK_SECRET=your_secret_token_here
   ```

## 4. Activation

Make sure your app is activated. Once activated, whenever someone joins a Zoom meeting hosted by the account that created the app (or a sub-account), Zoom will send a POST request to your webhook endpoint. The voice agent will filter these requests by the specific meeting ID you provide when starting the script.
