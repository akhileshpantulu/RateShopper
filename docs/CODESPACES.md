# Rate Shopper in a codespace

The Rate Shopper is starting. Its page opens in a new browser tab on its own; you do not need to type anything here.

If the tab did not open (a pop-up blocker can stop it):

1. Open the **Ports** tab at the bottom of this window.
2. Hover over the row labelled **Rate Shopper** (port 8765).
3. Click the globe icon, **Open in Browser**.

The page address stays private: only you, signed in to GitHub, can open it.

## Getting updates

Each time the codespace starts it takes the latest version from `main`. To update one that is open: on GitHub, go to
**Code**, **Codespaces**, open the **...** menu next to your codespace, choose **Stop codespace**, then open it again.

## Good to know

- **Your data** (compsets, collected rates) is saved inside this codespace and is still there next time you open it
  from GitHub, under Code, Codespaces. Deleting the codespace deletes the data. GitHub also deletes codespaces left
  unused for 30 days by default; the retention period is under GitHub Settings, Codespaces.
- **Long collections:** a codespace stops after 30 minutes without activity by default, which also stops a collection
  in progress. For a full collection, raise the idle timeout (up to 240 minutes) under GitHub Settings, Codespaces.
  Starting the collection again resumes it; rates already collected that day are kept.
- **Cost:** personal GitHub accounts include free monthly codespace hours; this app runs on the smallest machine.
