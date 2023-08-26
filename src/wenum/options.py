from typing import Optional
from .exception import (
    FuzzExceptBadRecipe,
    FuzzExceptBadOptions,
    FuzzExceptBadFile,
)
from os.path import isfile
from .facade import (
    Facade,
    ERROR_CODE,
)

from .factories.fuzzresfactory import resfactory
from .factories.dictfactory import dictionary_factory
from .fuzzobjects import FuzzStats
from .filters.ppfilter import FuzzResFilter
from .filters.simplefilter import FuzzResSimpleFilter
from .helpers.str_func import json_minify

from .core import Fuzzer
from .myhttp import HttpPool

from .externals.reqresp.cache import HttpCache

from collections import defaultdict, UserDict

from .printers import JSON
import json

# The priority moves in steps of 10 to allow a buffer zone for future finegrained control. This way, one group of
# requests (such as within a seed) has leverage over queuing with less prio than the other requests while being
# prioritized higher than the next group of requests (e.g. the next seed)
PRIORITY_STEP = 10


class FuzzSession(UserDict):
    def __init__(self, parsed_args=None):
        print("Tes")
        self.url = ""
        self.wordlist_list = []
        self.colorless = None
        #TODO this if statement is only temporary, will be necessary soon enough
        if parsed_args:
            self.validate_args(parsed_args)


        self.data: dict = self._defaults()
        self.keys_not_to_dump = [
            "recipe",
            "seed_payload",
            "compiled_seed",
            "compiled_stats",
            "compiled_dictio",
            "compiled_simple_filter",
            "compiled_filter",
            "compiled_prefilter",
            "compiled_printer",
            "description",
            "transport",
        ]

        ## recipe must be superseded by options
        #if "recipe" in kwargs and kwargs["recipe"]:
        #    for recipe in kwargs["recipe"]:
        #        self.import_from_file(recipe)

        #self.update(kwargs)

        self.cache: HttpCache = HttpCache()
        self.http_pool: Optional[HttpPool] = None

        self.stats = FuzzStats()

    def validate_args(self, parsed_args):
        print(parsed_args)
        if parsed_args.url is None or "FUZZ" not in parsed_args.url:
            raise FuzzExceptBadOptions(
                "Specify the URL either with -u and supply a FUZZ keyword. "
            )
        self.url = parsed_args.url

        print("a")
        print(parsed_args.wordlist)
        if not parsed_args.wordlist:
            raise FuzzExceptBadOptions("Bad usage: You must specify a payload.")
        for wordlist in parsed_args.wordlist:
            if not isfile(wordlist):
                raise FuzzExceptBadFile("File does not exist")
            self.wordlist_list.append(wordlist)

        self.colorless = parsed_args.colorless



    @staticmethod
    def _defaults():
        return dict(
            hs=None,
            hc=[],
            hw=[],
            hl=[],
            hh=[],
            ss=None,
            sc=[],
            sw=[],
            sl=[],
            sh=[],
            payloads=None,
            limitrequests=False,
            LIMITREQUESTS_THRESHOLD=20000,
            auto_filter=False,
            follow_redirects=False,
            iterator=None,
            printer=(None, None),
            colour=True,
            progress_bar=True,
            verbose=False,
            interactive=False,
            hard_filter=False,
            transport="http/s",
            recipe=[],
            proxies=None,
            conn_delay=int(Facade().settings.get("connection", "conn_delay")),
            req_delay=int(Facade().settings.get("connection", "req_delay")),
            retries=int(Facade().settings.get("connection", "retries")),
            rlevel=0,
            plugin_rlevel=0,
            scanmode=True,
            delay=None,
            concurrent=int(Facade().settings.get("connection", "concurrent")),
            url="",
            domain_scope=False,
            method=None,
            auth={},
            postdata=None,
            headers=[],
            cookie=[],
            script="",
            script_args={},
            connect_to_ip=None,
            # Session keeps track of current prio level to be assigned to requests.
            # Useful to poll which prio level the next seed should receive, and increase by that amount
            current_priority_level=PRIORITY_STEP,
            # this is equivalent to payloads but in a different format
            dictio=None,
            # these will be compiled
            seed_payload=False,
            filter="",
            prefilter=[],
            compiled_filter=None,
            compiled_prefilter=[],
            compiled_printer=None,
            compiled_seed=None,
            compiled_stats=None,
            compiled_dictio=None,
            runtime_log=None,)

    def update(self, options):
        self.data.update(options)

    def validate(self):
        error_list = []

        if self.data["rlevel"] > 0 and self.data["transport"] == "dryrun":
            error_list.append(
                "Bad usage: Recursion cannot work without making any HTTP request."
            )

        if self.data["script"] and self.data["transport"] == "dryrun":
            error_list.append(
                "Bad usage: Plugins cannot work without making any HTTP request."
            )

        if self.data["hs"] and self.data["ss"]:
            raise FuzzExceptBadOptions(
                "Bad usage: Hide and show regex filters flags are mutually exclusive. Only one could be specified.")

        if self.data["rlevel"] < 0:
            raise FuzzExceptBadOptions(
                "Bad usage: Recursion level must be a positive int."
            )

        if self.data["proxies"]:
            for ip, port, ttype in self.data["proxies"]:
                if ttype not in ("SOCKS5", "SOCKS4", "HTTP"):
                    raise FuzzExceptBadOptions(
                        "Bad proxy type specified, correct values are HTTP, SOCKS4 or SOCKS5.")

        return error_list

    def export_to_file(self, filename):
        """
        Probably broken, needs to be fixed to be functional
        """
        try:
            with open(filename, "w") as f:
                json_options = json.dumps(self.export_active_options_dict(), sort_keys=True)
                f.write(json_options)
        except IOError:
            raise FuzzExceptBadFile("Error writing recipe file.")

    def import_from_file(self, filename):
        try:
            with open(filename, "r") as file:
                self.import_json(file.read())
        except IOError:
            raise FuzzExceptBadFile("Error loading recipe file {}.".format(filename))
        except json.decoder.JSONDecodeError as e:
            raise FuzzExceptBadRecipe(
                "Incorrect JSON recipe {} format: {}".format(filename, str(e))
            )

    def assign_next_priority_level(self):
        """
        Pulls current priority level, increases it and returns the value. Useful for assigning new level
        to new recursions
        """
        self.data["current_priority_level"] += PRIORITY_STEP
        return self.data["current_priority_level"]

    def import_json(self, data):
        """
        Load options stored as JSON into memory
        """
        json_data = json.loads(json_minify(data))

        try:
            if "wenum_recipe" in json_data and json_data["wenum_recipe"]["recipe_version"] == "0.3":
                for key, value in json_data["wenum_recipe"].items():
                    if key not in self.keys_not_to_dump:
                        if key in self.data and isinstance(self.data[key], list):
                            self.data[key] += value
                        else:
                            self.data[key] = value
            else:
                raise FuzzExceptBadRecipe("Unsupported recipe version.")
        except KeyError:
            raise FuzzExceptBadRecipe("Incorrect recipe format.")

    def export_active_options_dict(self) -> dict:
        """
        Returns active options as a dictionary
        """
        active_options_dict = dict(wenum_recipe=defaultdict(dict))
        defaults = self._defaults()

        for key, value in self.data.items():
            # Only dump the non-default options
            if key not in self.keys_not_to_dump and value != defaults[key]:
                active_options_dict["wenum_recipe"][key] = self.data[key]
        active_options_dict["wenum_recipe"]["recipe_version"] = "0.3"

        return active_options_dict

    def payload(self, **kwargs):
        try:
            self.data.update(kwargs)
            self.compile_seeds()
            self.compile_dictio()
            for r in self.data["compiled_dictio"]:
                yield tuple((fuzz_word.content for fuzz_word in r))
        finally:
            self.data["compiled_dictio"].cleanup()

    def fuzz(self, **kwargs):
        self.data.update(kwargs)

        fz = None
        try:
            fz = Fuzzer(self.compile())

            for f in fz:
                yield f

        finally:
            if fz:
                fz.cancel_job()
                self.stats.update(self.data["compiled_stats"])

            if self.http_pool:
                self.http_pool.deregister()
                self.http_pool = None

    def __enter__(self):
        self.http_pool = HttpPool(self)
        self.http_pool.register()
        return self

    def __exit__(self, *args):
        self.close()

    def get_fuzz_words(self) -> set:
        fuzz_words = self.data["compiled_filter"].get_fuzz_words()

        for comp_obj in ["compiled_seed"]:
            if self.data[comp_obj]:
                fuzz_words += self.data[comp_obj].payload_man.get_fuzz_words()

        for prefilter in self.data["compiled_prefilter"]:
            fuzz_words += prefilter.get_fuzz_words()

        return set(fuzz_words)

    def compile_dictio(self):
        self.data["compiled_dictio"] = dictionary_factory.create(
            "dictio_from_options", self
        )

    def compile_seeds(self):
        self.data["compiled_seed"] = resfactory.create("seed_from_options", self)

    def compile(self):
        """
        Sets some things before actually running
        """
        # Validate options
        error = self.validate()
        if error:
            raise FuzzExceptBadOptions(error[0])

        self.data["seed_payload"] = True if self.data["url"] == "FUZZ" else False

        # printer
        try:
            filename, printer = self.data["printer"]
        except ValueError:
            raise FuzzExceptBadOptions(
                "Bad options: Printer must be specified in the form of ('filename', 'printer')")

        if filename:
            self.data["compiled_printer"] = JSON(filename)

        try:
            for filter_option in ["hc", "hw", "hl", "hh", "sc", "sw", "sl", "sh"]:
                self.data[filter_option] = [
                    ERROR_CODE
                    if i == "XXX"
                    else int(i)
                    for i in self.data[filter_option]
                ]
        except ValueError:
            raise FuzzExceptBadOptions(
                "Bad options: Filter must be specified in the form of [int, ... , int, XXX]."
            )

        self.compile_seeds()
        self.compile_dictio()

        # filter options
        self.data["compiled_simple_filter"] = FuzzResSimpleFilter.from_options(self)
        self.data["compiled_filter"] = FuzzResFilter(self.data["filter"])
        for prefilter in self.data["prefilter"]:
            self.data["compiled_prefilter"].append(
                FuzzResFilter(filter_string=prefilter)
            )

        # This line takes a long time to execute (for big wordlists?)
        self.data["compiled_stats"] = FuzzStats.from_options(self)

        # Check payload num
        fuzz_words = self.get_fuzz_words()

        if self.data["compiled_dictio"].width() != len(fuzz_words):
            raise FuzzExceptBadOptions("FUZZ words and number of payloads do not match!")

        if self.data["script"]:
            Facade().scripts.kbase.update(self.data["script_args"])

            for k, v in Facade().settings.get_section("kbase"):
                if k not in self.data["script_args"]:
                    Facade().scripts.kbase[k] = v

        if not self.http_pool:
            self.http_pool = HttpPool(self)
            self.http_pool.register()

        if self.data["colour"]:
            Facade().printers.kbase["colour"] = False

        if self.data["verbose"]:
            Facade().printers.kbase["verbose"] = True

        return self

    def close(self):
        if self.data["compiled_dictio"]:
            self.data["compiled_dictio"].cleanup()

        if self.http_pool:
            self.http_pool.deregister()
            self.http_pool = None
