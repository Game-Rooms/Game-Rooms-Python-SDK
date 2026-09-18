from setuptools import find_packages, setup


setup(
    name="game-rooms",
    version="1.0.0",
    description="Python SDK for the Game Rooms Worker protocol",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    include_package_data=True,
    install_requires=["websocket-client>=1.8.0,<2"],
    python_requires=">=3.9",
)
