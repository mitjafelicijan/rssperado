import argostranslate.package
import argostranslate.translate

# from_code = "es"


translatedText = argostranslate.translate.translate("Hello World", "en", "es")
print(translatedText)

# Download and install Argos Translate package
# argostranslate.package.update_package_index()
# available_packages = argostranslate.package.get_available_packages()
# language_list = ["ar", "az", "ca", "zh", "cs", "da", "nl", "eo", "fi", "fr", "de", "el", "he", "hi", "hu", "id", "ga", "it", "ja", "ko", "fa", "pl", "pt", "ru", "sk", "es", "sv", "th", "tr"]
# for idx, from_code in enumerate(language_list):
#     to_code = "en"
#     package_to_install = next(
#         filter(
#             lambda x: x.from_code == from_code and x.to_code == to_code, available_packages
#         )
#     )
#     print("  - [{}/{}] Installing transaltion for '{}'".format(idx+1, len(language_list), package_to_install))
#     argostranslate.package.install_from_path(package_to_install.download())

#     # Translate
#     # translatedText = argostranslate.translate.translate("Hello World", from_code, to_code)
#     # print(translatedText)
#     # '¡Hola Mundo!'
