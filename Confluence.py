import json
import re
import sys
from pathlib import Path

import requests
import sublime
import sublime_plugin

try:
    import lxml.html
    HTML_PRETTIFY = True
except ImportError:
    HTML_PRETTIFY = False


sys.path.append(str(Path(__file__).resolve().parent))
import markdown2


class ConfluenceApi:

    def __init__(self, username, password, base_uri, *, verify_ssl=True):
        self.username = username
        self.password = password
        self.base_uri = base_uri
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.session.auth = requests.auth.HTTPBasicAuth(self.username, self.password)
        print("ConfluenceApi username: {}, password: {}, base_uri: {}".format(
            self.username, "*" * len(self.password), self.base_uri))

    def _request(self, method, sub_uri, params=None, **kwargs):
        url = f"{self.base_uri}/{sub_uri}"
        headers = {"Content-Type": "application/json"}
        if params:
            kwargs.update(params=params)
        # Ensure we are authenticated (set cookie, session, etc.)
        self.session.request("get", self.base_uri)
        # Make the "real" call
        response = self.session.request(
            method, url, headers=headers, **kwargs)
        return response

    def _post(self, url, data=None):
        return self._request("post", url, data=json.dumps(data))

    def _get(self, url, params=None):
        return self._request("get", url, params=params)

    def _put(self, url, data=None):
        return self._request("put", url, data=json.dumps(data))

    def _delete(self, url, params=None):
        return self._request("delete", url, params=params)

    def create_content(self, content_data):
        return self._post("content/", data=content_data)

    def search_content(self, space_key, title):
        cql = f'type=page AND space="{space_key}" AND title~"{title}"'
        params = {"cql": cql}
        response = self._get("content/search", params=params)
        return response

    def get_content_by_id(self, content_id):
        response = self._get(
            f"content/{content_id}?expand=body.storage,version,space")
        return response

    def get_content_by_title(self, space_key, title):
        cql = f'type=page AND space="{space_key}" AND title="{title}"'
        params = {"cql": cql}
        response = self._get("content/search", params=params)
        return response

    def get_content_history(self, content_id):
        return self._get(f"content/{content_id}/history")

    def get_content_uri(self, content):
        base = content["_links"]["base"]
        webui = content["_links"]["webui"]
        return f"{base}{webui}"

    def update_content(self, content_id, content_data):
        return self._put(f"content/{content_id}",
                         data=content_data)

    def delete_content(self, content_id):
        return self._delete(f"content/{content_id}")


class Markup:
    def __init__(self):
        self.markups = {"Markdown": self.markdown_to_html,
            "Markdown Extended": self.markdown_to_html,
            "Markdown (Standard)": self.markdown_to_html,
            "reStructuredText": self.rst_to_html}

    def markdown_to_html(self, content):
        return markdown2.markdown(
            content, extras=["tables", "fenced-code-blocks", "strike"],
        ).encode("utf-8").decode()

    def rst_to_html(self, content):
        try:
            from docutils.core import publish_string  # noqa: PLC0415
            return publish_string(content, writer_name="html")
        except ImportError:
            error_msg = """
            RstPreview requires docutils to be installed for the python interpreter that Sublime uses.
            run: `sudo easy_install-2.6 docutils` and restart Sublime (if on Mac OS X or Linux).
            For Windows check the docs at https://github.com/d0ugal/RstPreview
            """
            sublime.error_message(error_msg)
            raise

    def to_html(self, content, syntax):
        syntax = syntax.split(".")[0].split("/")[-1]
        if syntax not in self.markups:
            sublime.error_message(f"Not support {syntax} syntax yet")
            return None
        converter = self.markups[syntax]
        new_content = converter(content)
        if not new_content:
            sublime.error_message(
                "Can not parse this document.")
        return new_content

    def get_meta_and_content(self, contents):
        meta = {}
        content = []
        tmp = contents.splitlines()
        for x, entry in enumerate(tmp):
            if entry.strip():
                if re.match(r"[Ss]pace: *", entry):
                    meta["space_key"] = re.sub("[^:]*: *", "", entry)
                elif re.match(r"[Aa]ncestor Title: *", entry):
                    meta["ancestor_title"] = re.sub("[^:]*: *", "", entry)
                elif re.match(r"[Tt]itle: *", entry):
                    meta["title"] = re.sub("[^:]*: *", "", entry)
            else:
                content = tmp[x + 1:]
                break
        return (meta, content)


class BaseConfluencePageCommand(sublime_plugin.TextCommand):
    """Base class for Confluence commands. Handles authentication."""

    MSG_USERNAME = "Confluence username:"
    MSG_PASSWORD = "Confluence password:"  # noqa: S105
    hidden_string = ""
    callback = None

    def run(self, edit):
        self.edit = edit
        settings = sublime.load_settings("Confluence.sublime-settings")
        self.base_uri = settings.get("base_uri")
        self.username = settings.get("username")
        self.password = settings.get("password") or ""
        self.default_space_key = settings.get("default_space_key")
        self.verify_ssl = settings.get("verify_ssl", True)

    def get_credential(self):
        if not self.username and not self.password:
            sublime.status_message("Waiting for username")
            sublime.set_timeout(self.get_username_password, 50)
        elif not self.username:
            sublime.status_message("Waiting for username")
            sublime.set_timeout(self.get_username, 50)
        elif not self.password:
            sublime.status_message("Waiting for password")
            sublime.set_timeout(self.get_password, 50)
        else:
            callback = self.callback
            if callback:
                self.callback = None
                sublime.set_timeout(callback, 50)

    def get_username_password(self):
        self.view.window().show_input_panel(
            self.MSG_USERNAME, "", self.on_done_username_password, None, None)

    def get_username(self):
        self.view.window().show_input_panel(
            self.MSG_USERNAME, "", self.on_done_username, None, None)

    def get_password(self):
        self.view.window().show_input_panel(
            self.MSG_PASSWORD, "", self.on_done_password, self.on_change_password, None)

    def on_done_username_password(self, value):
        self.username = value
        sublime.status_message("Waiting for password")
        sublime.set_timeout(self.get_password, 50)

    def on_done_username(self, value):
        self.username = value
        sublime.set_timeout(self.get_confluence_api, 50)

    def on_done_password(self, _value):
        callback = self.callback
        if callback:
            self.callback = None
            sublime.set_timeout(callback, 50)

    def parse_input_password(self, input_password):
        length = len(input_password)
        for index, _ in enumerate(input_password, 1):
            if _ != "*":
                character = _
                position = index
                break
        else:
            character = "*"
            position = length
        return (length, character, position)

    def on_change_password(self, value):
        # Known issue
        # It can not get correct password when user modify the password inline
        if value != self.hidden_string:
            if len(value) < len(self.password):
                self.password = self.password[:len(value)]
            elif len(value) == len(self.password):
                (length, character, position) = self.parse_input_password(value)
                password = self.password[:length]
                self.password = password[:position - 1] + character + password[position:]
            else:
                (length, character, position) = self.parse_input_password(value)
                password = self.password
                self.password = password[:position - 1] + character + password[position - 1:]
            self.hidden_string = "*" * len(value)
            self.view.window().run_command("hide_panel", {"cancel": False})
            self.view.window().show_input_panel(
                self.MSG_PASSWORD, self.hidden_string, self.on_done_password,
                self.on_change_password, None)


class PostConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SUCCESS = "Content created and the url copied to the clipboard."

    def run(self, edit):
        super().run(edit)
        self.callback = self.post
        sublime.set_timeout(self.get_credential, 50)

    def post(self):
        region = sublime.Region(0, self.view.size())
        contents = self.view.substr(region)
        markup = Markup()
        meta, content = markup.get_meta_and_content(contents)
        syntax = self.view.settings().get("syntax")
        new_content = markup.to_html("\n".join(content), syntax)
        if not new_content:
            return
        self.confluence_api = ConfluenceApi(
            self.username, self.password, self.base_uri,
            verify_ssl=self.verify_ssl)
        response = self.confluence_api.get_content_by_title(
            meta["space_key"], meta["ancestor_title"])
        if response.ok:
            ancestor = response.json()["results"][0]
            ancestor_id = int(ancestor["id"])
            space = {"key": meta["space_key"]}
            body = {"storage": {"value": new_content, "representation": "storage"}}
            data = {"type": "page", "title": meta["title"], "ancestors": [{"id": ancestor_id}],
                        "space": space, "body": body}
            result = self.confluence_api.create_content(data)
            if result.ok:
                self.view.settings().set("confluence_content", result.json())
                # copy content url
                content_uri = self.confluence_api.get_content_uri(result.json())
                sublime.set_clipboard(content_uri)
                sublime.status_message(self.MSG_SUCCESS)
            else:
                print(result.text)
                sublime.error_message(f"Can not create content, reason: {result.reason}")
        else:
            print(response.text)
            sublime.error_message(f"Can not get ancestor, reason: {response.reason}")


class GetConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SPACE_KEY = "Confluence space key:"
    MSG_SEARCH_PAGE = "Page title:"
    MSG_SUCCESS = "Content url copied to the clipboard."
    all_space = False
    specific_space_key = False

    def run(self, edit):
        super().run(edit)
        self.callback = self.get_space_key_and_page_title
        sublime.set_timeout(self.get_credential, 50)

    def get_space_key_and_page_title(self):
        if self.all_space:
            self.space = None
            sublime.set_timeout(self.get_page_title, 50)
        elif self.specific_space_key or not self.default_space_key:
            sublime.set_timeout(self.get_space_key, 50)
        else:
            self.space_key = self.default_space_key
            sublime.set_timeout(self.get_page_title, 50)

    def get_space_key(self):
        sublime.status_message("Waiting for space key")
        self.view.window().show_input_panel(
            self.MSG_SPACE_KEY, "", self.on_done_space_key, None, None)

    def get_page_title(self):
        sublime.status_message("Waiting for page title")
        self.view.window().show_input_panel(
            self.MSG_SEARCH_PAGE, "", self.on_done_page_title, None, None)

    def on_done_space_key(self, value):
        self.space_key = value
        sublime.set_timeout(self.get_page_title, 50)

    def on_done_page_title(self, value):
        self.page_title = value
        sublime.set_timeout(self.get_pages, 50)

    def get_pages(self):
        self.confluence_api = ConfluenceApi(
            self.username, self.password, self.base_uri,
            verify_ssl=self.verify_ssl)
        response = self.confluence_api.search_content(self.space_key, self.page_title)
        if response.ok:
            self.pages = response.json()["results"]
            packed_pages = [page["title"] for page in self.pages]
            if packed_pages:
                self.view.window().show_quick_panel(packed_pages, self.on_done_pages)
            else:
                sublime.error_message(f"No result found for {self.page_title}")
        else:
            print(response.text)
            sublime.error_message(f"Can not get pages, reason: {response.reason}")

    def on_done_pages(self, idx):
        if idx == -1:
            return
        content_id = self.pages[idx]["id"]
        response = self.confluence_api.get_content_by_id(content_id)
        if response.ok:
            content = response.json()
            body = content["body"]["storage"]["value"]
            if HTML_PRETTIFY:
                document_root = lxml.html.fromstring(body)
                body = (lxml.etree.tostring(document_root, encoding="unicode", pretty_print=True))

            new_view = self.view.window().new_file()
            # set syntax file
            new_view.set_syntax_file("Packages/HTML/HTML.sublime-syntax")
            new_view.settings().set("auto_indent", False)

            # insert the page
            new_view.run_command("insert", {"characters": body})
            new_view.set_name(content["title"])
            new_view.settings().set("confluence_content", content)
            new_view.settings().set("auto_indent", True)
            new_view.run_command("reindent", {"single_line": False})
            new_view.run_command("expand_tabs", {"set_translate_tabs": True})

            # copy content url
            content_uri = self.confluence_api.get_content_uri(content)
            sublime.set_clipboard(content_uri)
            sublime.status_message(self.MSG_SUCCESS)
        else:
            print(response.text)
            sublime.error_message(f"Can not get content, reason: {response.reason}")


class UpdateConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SUCCESS = "Page updated and url copied to the clipboard."

    def run(self, edit):
        super().run(edit)
        self.content = self.view.settings().get("confluence_content")
        if self.content:
            self.callback = self.update_from_editor
        else:
            self.callback = self.update_from_source
        sublime.set_timeout(self.get_credential, 50)

    def update_from_editor(self):
        content_id = self.content["id"]
        title = self.content["title"]
        space_key = self.content["space"]["key"]
        version_number = self.content["version"]["number"] + 1
        region = sublime.Region(0, self.view.size())
        contents = self.view.substr(region)
        syntax = self.view.settings().get("syntax")
        if "HTML" in syntax:
            new_content = "".join(contents.split("\n"))
        else:
            markup = Markup()
            _, content = markup.get_meta_and_content(contents)
            new_content = markup.to_html("\n".join(content), syntax)

        space = {"key": space_key}
        version = {"number": version_number, "minorEdit": False}
        body = {"storage": {"value": new_content, "representation": "storage"}}
        data = {"id": content_id, "type": "page", "title": title,
                    "space": space, "version": version, "body": body}
        try:
            self.confluence_api = ConfluenceApi(
                self.username, self.password, self.base_uri,
                verify_ssl=self.verify_ssl)
            response = self.confluence_api.update_content(content_id, data)
            if response.ok:
                content_uri = self.confluence_api.get_content_uri(self.content)
                sublime.set_clipboard(content_uri)
                sublime.status_message(self.MSG_SUCCESS)
                self.view.settings().set("confluence_content", response.json())
            else:
                print(response.text)
                sublime.error_message(f"Can't update content, reason: {response.reason}")
        except requests.exceptions.RequestException as error:
            sublime.error_message(f"Can't update content: {error}")

    def update_from_source(self):
        region = sublime.Region(0, self.view.size())
        contents = self.view.substr(region)
        markup = Markup()
        meta, content = markup.get_meta_and_content(contents)
        syntax = self.view.settings().get("syntax")
        new_content = markup.to_html("\n".join(content), syntax)
        if not new_content:
            sublime.error_message(
                "Can't update: this doesn't appear to be a valid Confluence page.")
            return
        self.confluence_api = ConfluenceApi(
            self.username, self.password, self.base_uri,
            verify_ssl=self.verify_ssl)

        get_content_by_title_resp = self.confluence_api.get_content_by_title(
            meta["space_key"], meta["title"])
        if get_content_by_title_resp.ok:
            content_id = get_content_by_title_resp.json()["results"][0]["id"]

            get_content_by_id_resp = self.confluence_api.get_content_by_id(content_id)
            if get_content_by_id_resp.ok:
                content = get_content_by_id_resp.json()
                space = {"key": meta["space_key"]}
                version_number = content["version"]["number"] + 1
                version = {"number": version_number, "minorEdit": False}
                # ancestor_id = int(ancestor["id"])
                body = {"storage": {"value": new_content, "representation": "storage"}}
                data = {"id": content_id, "type": "page", "title": meta["title"],
                            "space": space, "version": version, "body": body}

                update_content_resp = self.confluence_api.update_content(content_id, data)
                if update_content_resp.ok:
                    self.view.settings().set("confluence_content", update_content_resp.json())
                    content_uri = self.confluence_api.get_content_uri(update_content_resp.json())
                    sublime.set_clipboard(content_uri)
                    sublime.status_message(self.MSG_SUCCESS)
                else:
                    print(update_content_resp.text)
                    sublime.error_message(f"Can not update content, reason: {update_content_resp.reason}")
            else:
                print(get_content_by_id_resp.text)
                sublime.error_message(f"Can not get content by id, reason: {get_content_by_id_resp.reason}")
        else:
            print(get_content_by_title_resp.text)
            sublime.error_message(f"Can not get content by title, reason: {get_content_by_title_resp.reason}")


class DeleteConfluencePageCommand(BaseConfluencePageCommand):
    MSG_SUCCESS = "Confluence page has been deleted."

    def run(self, edit):
        super().run(edit)
        self.content = self.view.settings().get("confluence_content")
        if not self.content:
            sublime.error_message(
                "Can't update: this doesn't appear to be a valid Confluence page.")
            return
        self.callback = self.delete
        sublime.set_timeout(self.get_credential, 50)

    def delete(self):
        content_id = str(self.content["id"])
        try:
            self.confluence_api = ConfluenceApi(
                self.username, self.password, self.base_uri,
                verify_ssl=self.verify_ssl)
            response = self.confluence_api.delete_content(content_id)
            if response.ok:
                sublime.status_message(self.MSG_SUCCESS)
            else:
                print(response.text)
                sublime.error_message(f"Can't delete content, reason: {response.reason}")
        except requests.exceptions.RequestException as error:
            sublime.error_message(f"Can't delete content: {error}")
