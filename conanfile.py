from conans import ConanFile, tools
from conan.tools.cmake import CMakeDeps, CMake, CMakeToolchain
from conans.tools import SystemPackageTool
from conan.errors import ConanException
import os
import shutil
from pathlib import Path, PurePosixPath
import subprocess

required_conan_version = ">=1.60.0"


class XeusConan(ConanFile):
    python_requires = "bundleutils/0.1@lkeb/stable"
    python_requires_extend = "bundleutils.BundleUtils"
    name = "xeus"
    version = "3.1.4"
    license = "MIT"
    author = "B. van Lew b.van_lew@lumc.nl"
    url = "https://github.com/jupyter-xeus/xeus.git"
    description = """xeus is a library meant to facilitate 
    the implementation of kernels for Jupyter"""
    topics = ("python", "jupyter")
    settings = "os", "compiler", "build_type", "arch"
    options = {"shared": [True, False], "testing": [True, False], 'merge_package': [True, False]}
    default_options = {"shared": True, "testing": False, 'merge_package': False}
    generators = "CMakeDeps"
    exports = "cmake/*"
    requires = (
        "nlohmann_json/3.11.3",
        "xtl/0.7.5"
    )

    def init(self):
        # use the buntilutils to record the 
        # original source directory
        self._save_git_path()

    def source(self):
        try:
            self.run(f"git clone {self.url}")
        except ConanException as e:
            print(e)
        os.chdir("./xeus")
        try:
            self.run(f"git checkout tags/{self.version}")
        except ConanException as e:
            print(e)
        
        cmakepath = os.path.join(self.source_folder, "xeus", "CMakeLists.txt")
        ## for CMP0091 policy set xeus CMake version to 3.15
        tools.replace_in_file(cmakepath, "cmake_minimum_required(VERSION 3.8)", "cmake_minimum_required(VERSION 3.15)")
        ## Make a combined debug/release/relwithdebug package
        tools.replace_in_file(cmakepath, "set(XEUS_CMAKECONFIG_INSTALL_DIR \"${CMAKE_INSTALL_LIBDIR}/cmake/${PROJECT_NAME}\"", "set(XEUS_CMAKECONFIG_INSTALL_DIR \"lib/cmake/${PROJECT_NAME}\"")
        tools.replace_in_file(cmakepath, "FILE ${PROJECT_NAME}Targets.cmake", "")
        configinpath = os.path.join(self.source_folder, "xeus", "xeusConfig.cmake.in")
        ## Fixe the targets file name to match the above change
        tools.replace_in_file(configinpath, "include(\"${CMAKE_CURRENT_LIST_DIR}/@PROJECT_NAME@Targets.cmake\")", "include(\"${CMAKE_CURRENT_LIST_DIR}/@PROJECT_NAME@-targets.cmake\")")
        ## Add post build install command to support packaging
        install_text = """
add_custom_command(TARGET ${xeus_targets} POST_BUILD
    COMMAND "${CMAKE_COMMAND}"
    --install ${CMAKE_CURRENT_BINARY_DIR}
    --config $<CONFIGURATION>
    --prefix ${MV_INSTALL_DIR}/$<CONFIGURATION>
)

"""
        with open(cmakepath, "a") as cmakefile:
            cmakefile.write(install_text)

        os.chdir("..")

    def _get_tc(self):
        """Generate the CMake configuration using
        multi-config generators on all platforms, as follows:

        Windows - defaults to Visual Studio
        Macos - XCode
        Linux - Ninja Multi-Config

        CMake needs to be at least 3.17 for Ninja Multi-Config

        Returns:
            CMakeToolchain: a configured toolchain object
        """
        generator = None
        if self.settings.os == "Macos":
            generator = "Xcode"

        if self.settings.os == "Linux":
            generator = "Ninja Multi-Config"


        tc = CMakeToolchain(self, generator=generator)
        tc.variables["BUILD_TESTING"] = "ON" if self.options.testing else "OFF"
        tc.variables["BUILD_SHARED_LIBS"] = "ON" if self.options.shared else "OFF"
        nj_path = Path(self.deps_cpp_info["nlohmann_json"].rootpath)
        xtl_path = Path(self.deps_cpp_info["xtl"].rootpath)
        tc.variables["CMAKE_PREFIX_PATH"] = Path(self.build_folder).as_posix()

        if self.settings.os == "Linux":
            tc.variables["CMAKE_CONFIGURATION_TYPES"] = "Debug;Release;RelWithDebInfo"

        if self.settings.os == "Macos":
            proc = subprocess.run(
                "brew --prefix libomp", shell=True, capture_output=True
            )
            prefix_path = f"{proc.stdout.decode('UTF-8').strip()}"
            tc.variables["OpenMP_ROOT"] = prefix_path

        return tc

    def layout(self):
        # Cause the libs and bin to be output to separate subdirs
        # based on build configuration.
        self.cpp.package.libdirs = ["lib/$<CONFIG>"]
        self.cpp.package.bindirs = ["bin/$<CONFIG>"]

    def system_requirements(self):
        if self.settings.os == "Macos":
            installer = SystemPackageTool()
            installer.install("libomp")
            # Make the brew OpenMP findable with a symlink
            proc = subprocess.run("brew --prefix libomp",  shell=True, capture_output=True)
            subprocess.run(f"ln {proc.stdout.decode('UTF-8').strip()}/lib/libomp.dylib /usr/local/lib/libomp.dylib", shell=True)

    def generate(self):
        print("In generate")
        tc = self._get_tc()
        tc.generate()
        deps = CMakeDeps(self)
        deps.generate()
        with open("conan_toolchain.cmake", "a") as toolchain:
            toolchain.write(
                fr"""
include_directories({Path(self.deps_cpp_info['xtl'].rootpath, 'include').as_posix()} {Path(self.deps_cpp_info['nlohmann_json'].rootpath, 'include').as_posix()})
            """
            )

    def _configure_cmake(self):
        cmake = CMake(self)
        cmake.verbose = True
        build_folder = os.path.join(self.build_folder, "xeus")
        print(f"Source folder {Path(self.source_folder).as_posix()}")
        try:
            cmake.configure(build_script_folder="xeus") #, cli_args=["--trace"])
        except ConanException as e:
            print(f"Exception: {e} from cmake invocation: \n Completing configure")

        return cmake

    def build(self):
        self._save_package_id()
        # Build both release and debug for dual packaging
        cmake = self._configure_cmake()

        cmake.build(build_type="Debug")
        cmake.install(build_type="Debug")

        cmake.build(build_type="Release")
        cmake.install(build_type="Release")

    # Package contains its own cmake config file
    def package_info(self):
        self.cpp_info.set_property("skip_deps_file", True)
        self.cpp_info.set_property("cmake_config_file", True)

    def _pkg_bin(self, build_type):
        print(f"Packaging artifacts for build type: {build_type}")
        src_dir = f"{self.build_folder}/{build_type}"
        dst_lib = f"lib/{build_type}"
        dst_bin = f"bin/{build_type}"

        self.copy("*.dll", src=src_dir, dst=dst_bin, keep_path=False)
        self.copy("*.so", src=src_dir, dst=dst_lib, keep_path=False)
        self.copy("*.dylib", src=src_dir, dst=dst_lib, keep_path=False)
        self.copy("*.a", src=src_dir, dst=dst_lib, keep_path=False)
        if ((build_type == "Debug") or (build_type == "RelWithDebInfo")) and (
            self.settings.compiler == "Visual Studio"
        ):
            # the debug info
            print("Adding pdb files for Windows debug")
            self.copy("*.pdb", src=src_dir, dst=dst_lib, keep_path=False)

    def package(self):
        # cleanup excess installs - this is a kludge TODO fix cmake
        print("cleanup")
        print(f"Package folder: {self.package_folder}")
        for child in Path(self.package_folder, "lib").iterdir():
            if child.is_file():
                child.unlink()
        print("end cleanup")
        self.copy("*.h", src="xeus/src/cpp", dst="include", keep_path=True)
        self.copy("*.hpp", src="xeus/src/cpp", dst="include", keep_path=True)

        self._pkg_bin(self.settings.build_type)
        # This allow the merging op multiple build_types into a single package
        self._merge_packages()
