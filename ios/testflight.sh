#!/bin/sh
# Archive RAGstofan and upload it to TestFlight. Needs the Apple account signed in to Xcode (Settings → Accounts)
# and an app record in App Store Connect for com.alexandersvarfdal.ragstofan.
set -e
cd "$(dirname "$0")"
xcodegen generate
xcodebuild -project RAGstofan.xcodeproj -scheme RAGstofan -configuration Release \
  -destination 'generic/platform=iOS' -archivePath build/RAGstofan.xcarchive -allowProvisioningUpdates archive
xcodebuild -exportArchive -archivePath build/RAGstofan.xcarchive -exportOptionsPlist ExportOptions.plist \
  -exportPath build/export -allowProvisioningUpdates
