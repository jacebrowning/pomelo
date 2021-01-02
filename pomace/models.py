from contextlib import suppress
from typing import Callable, List, Optional, Tuple

import log
from bs4 import BeautifulSoup
from datafiles import datafile, field, mapper
from selenium.common.exceptions import (
    ElementNotInteractableException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from splinter.driver.webdriver import WebDriverElement
from splinter.exceptions import ElementDoesNotExist

from . import prompts, shared
from .config import settings
from .enums import Mode, Verb
from .types import URL


__all__ = ["Locator", "Action", "Page"]


@datafile(order=True)
class Locator:

    mode: str = field(default="", compare=False)
    value: str = field(default="", compare=False)
    index: int = field(default=0, compare=False)
    uses: int = field(default=0, compare=True)

    def __repr__(self) -> str:
        return f"<locator {self.mode}={self.value}[{self.index}]>"

    def __bool__(self) -> bool:
        return bool(self.mode and self.value)

    def find(self) -> Optional[WebDriverElement]:
        elements = self._mode.finder(self.value)
        index = self.index
        try:
            element = elements[index]
            if index == 0 and not element.visible:
                log.debug(f"{self} found invisible element: {element.outer_html}")
                index += 1
                element = elements[index]
        except ElementDoesNotExist:
            log.debug(f"{self} unable to find element")
            return None
        else:
            self.index = index
            log.debug(f"{self} found element: {element.outer_html}")
            return element

    def score(self, value: int) -> bool:
        previous = self.uses

        if value > 0:
            self.uses = min(99, max(1, self.uses + value))
        else:
            self.uses = max(-1, self.uses + value)

        if self.uses == previous:
            return False

        result = "Increased" if self.uses > previous else "Decreased"
        log.debug(f"{result} {self} uses to {self.uses}")
        return True

    @property
    def _mode(self) -> Mode:
        return Mode(self.mode)


@datafile
class Action:

    verb: str = ""
    name: str = ""
    locators: List[Locator] = field(default_factory=lambda: [Locator()])

    @property
    def sorted_locators(self) -> List[Locator]:
        return [x for x in sorted(self.locators, reverse=True) if x]

    def __post_init__(self):
        if self.verb and len(self.locators) <= 1:
            for mode, value in self._verb.get_default_locators(self.name):
                self.locators.append(Locator(mode, value))

    def __str__(self):
        return f"{self.verb}_{self.name}"

    def __bool__(self) -> bool:
        return bool(self.verb and self.name)

    def __call__(self, *args, **kwargs) -> "Page":
        page = kwargs.pop("_page", None)
        page = self._call_method(page, *args, **kwargs)
        self.datafile.save()
        page.clean()
        return page

    def _call_method(self, page, *args, **kwargs) -> "Page":
        while self._trying_locators(*args, **kwargs):
            log.error(f"No locators able to find {self.name!r}")
            shared.linebreak = False
            mode, value = prompts.mode_and_value()
            if mode:
                self.locators.append(Locator(mode, value))
            else:
                break

        if page and self._verb.updates:
            return page

        return auto()

    def _trying_locators(self, *args, **kwargs) -> bool:
        if self._verb == Verb.TYPE:
            key = getattr(Keys, self.name.upper())
            function = ActionChains(shared.browser.driver).send_keys(key).perform
            self._perform_action(function, *args, **kwargs)
            return False

        for locator in self.sorted_locators:
            if locator:
                log.debug(f"Using {locator} to find {self.name!r}")
                element = locator.find()
                if element:
                    function = getattr(element, self.verb)
                    if self._perform_action(function, *args, **kwargs):
                        locator.score(+1)
                        return False
            locator.score(-1)

        return True

    def _perform_action(self, function: Callable, *args, **kwargs) -> bool:
        previous_url = shared.browser.url
        wait = kwargs.pop("wait", None)
        try:
            function(*args, **kwargs)
        except ElementDoesNotExist as e:
            log.warn(e)
            return False
        except ElementNotInteractableException as e:
            log.warn(e.msg)
            return False
        except WebDriverException as e:
            log.debug(e)
            return False
        else:
            self._verb.post_action(previous_url, wait)
            return True

    def clean(self, *, force: bool = False) -> int:
        unused_locators = []
        remove_unused_locators = force

        for locator in self.locators:
            if locator.uses <= 0:
                unused_locators.append(locator)
            if locator.uses >= 99:
                remove_unused_locators = True

        log.debug(f"Found {len(unused_locators)} unused locators for {self}")
        if not remove_unused_locators:
            return 0

        if unused_locators:
            log.info(f"Cleaning up locators for {self}")
            for locator in unused_locators:
                log.info(f"Removed unused {locator}")
                self.locators.remove(locator)

        return len(unused_locators)

    @property
    def _verb(self) -> Verb:
        return Verb(self.verb)


@datafile
class Locators:
    inclusions: List[Locator]
    exclusions: List[Locator]

    @property
    def sorted_inclusions(self) -> List[Locator]:
        return [x for x in sorted(self.inclusions, reverse=True) if x]

    @property
    def sorted_exclusions(self) -> List[Locator]:
        return [x for x in sorted(self.exclusions, reverse=True) if x]

    def clean(self, page, *, force: bool = False) -> int:
        unused_inclusion_locators = []
        unused_exclusion_locators = []
        remove_unused_locators = force

        for locator in self.inclusions:
            if locator.uses <= 0:
                unused_inclusion_locators.append(locator)
            if locator.uses >= 99:
                remove_unused_locators = True

        for locator in self.exclusions:
            if locator.uses <= 0:
                unused_exclusion_locators.append(locator)
            if locator.uses >= 99:
                remove_unused_locators = True

        count = len(unused_inclusion_locators) + len(unused_exclusion_locators)
        log.debug(f"Found {count} unused locators for {page}")
        if not remove_unused_locators:
            return 0

        if unused_inclusion_locators:
            log.info(f"Cleaning up inclusion locators for {page}")
            for locator in unused_inclusion_locators:
                log.info(f"Removed unused {locator}")
                self.inclusions.remove(locator)

        if unused_exclusion_locators:
            log.info(f"Cleaning up exclusion locators for {page}")
            for locator in unused_exclusion_locators:
                log.info(f"Removed unused {locator}")
                self.exclusions.remove(locator)

        return len(unused_inclusion_locators) + len(unused_exclusion_locators)


@datafile(
    "./sites/{self.domain}/{self.path}/{self.variant}.yml", defaults=True, manual=True
)
class Page:

    domain: str
    path: str = URL.ROOT
    variant: str = "default"

    locators: Locators = field(default_factory=lambda: Locators([], []))
    actions: List[Action] = field(default_factory=lambda: [Action()])

    @classmethod
    def at(cls, url: str, *, variant: str = "") -> "Page":
        if shared.browser.url != url:
            log.info(f"Visiting {url}")
            shared.browser.visit(url)

        if shared.browser.url != url:
            log.info(f"Redirected to {url}")

        kwargs = {"domain": URL(url).domain, "path": URL(url).path}
        variant = variant or URL(url).fragment
        if variant:
            kwargs["variant"] = variant

        return cls(**kwargs)  # type: ignore

    @property
    def url(self) -> URL:
        return URL(self.domain, self.path)

    @property
    def exact(self) -> bool:
        return "{" not in self.path

    @property
    def active(self) -> bool:
        log.debug(f"Determining if {self!r} is active")

        if self.url != URL(shared.browser.url):
            log.debug(f"{self!r} is inactive: URL not matched")
            return False

        log.debug("Checking that all expected elements can be found")
        for locator in self.locators.sorted_inclusions:
            if locator.find():
                if locator.score(+1):
                    self.datafile.save()
            else:
                log.debug(f"{self!r} is inactive: {locator!r} found expected element")
                return False

        log.debug("Checking that no unexpected elements can be found")
        for locator in self.locators.sorted_exclusions:
            if locator.find():
                if locator.score(+1):
                    self.datafile.save()
                log.debug(f"{self!r} is inactive: {locator!r} found unexpected element")
                return False

        log.debug(f"{self!r} is active")
        return True

    @property
    def text(self) -> str:
        return shared.browser.html

    @property
    def html(self) -> BeautifulSoup:
        return BeautifulSoup(self.text, "html.parser")

    def __repr__(self):
        if self.variant == "default":
            return f"Page.at('{self.url.value}')"
        return f"Page.at('{self.url.value}', variant='{self.variant}')"

    def __str__(self):
        if self.variant == "default":
            return f"{self.url}"
        return f"{self.url} ({self.variant})"

    def __dir__(self):
        names = []
        add_placeholder = True
        for action in self.actions:
            if action:
                names.append(str(action))
            else:
                add_placeholder = False
        if add_placeholder:
            self.actions.append(Action())
        return names

    def __getattr__(self, value: str) -> Action:
        if "_" in value:
            verb, name = value.split("_", 1)

            with suppress(FileNotFoundError):
                self.datafile.load()

            for action in self.actions:
                if action.name == name and action.verb == verb:
                    return action

            if Verb.validate(verb, name):
                action = Action(verb, name)
                setattr(
                    action, "datafile", mapper.create_mapper(action, root=self.datafile)
                )
                self.actions.append(action)
                return action

        return object.__getattribute__(self, value)

    def __contains__(self, value):
        return value in self.text

    def perform(self, name: str) -> Tuple["Page", bool]:
        action = getattr(self, name)
        if action.verb in {"fill", "select"}:
            value = settings.get_secret(action.name) or prompts.named_value(action.name)
            settings.update_secret(action.name, value)
            page = action(value, _page=self)
        else:
            page = action(_page=self)
        return page, page != self

    def clean(self, *, force: bool = False) -> int:
        count = self.locators.clean(self, force=force)

        unused_actions = []
        remove_unused_actions = force

        for action in self.actions:
            if all(locator.uses <= 0 for locator in action.locators):
                unused_actions.append(action)

        log.debug(f"Found {len(unused_actions)} unused actions for {self}")
        if unused_actions and remove_unused_actions:
            log.info(f"Cleaning up actions for {self}")
            for action in unused_actions:
                log.info(f"Removed unused {action}")
                self.actions.remove(action)

        for action in self.actions:
            count += action.clean(force=force)

        if count or force:
            self.datafile.save()

        return count


def auto() -> Page:
    matching_pages = []
    found_exact_match = False

    for page in Page.objects.filter(domain=URL(shared.browser.url).domain):
        if page.active:
            matching_pages.append(page)
        if page.exact:
            found_exact_match = True

    if found_exact_match:
        log.debug("Removing abstract pages from matches")
        matching_pages = [page for page in matching_pages if page.exact]

    if matching_pages:
        if len(matching_pages) > 1:
            for page in matching_pages:
                log.warn(f"Multiple pages matched: {page}")
                shared.linebreak = False
        return matching_pages[0]

    log.info(f"Creating new page: {shared.browser.url}")
    page = Page.at(shared.browser.url)
    page.datafile.save()
    return page
