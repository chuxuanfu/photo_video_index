import Foundation
import Darwin

var checks=[[String:Any]]()
for (index,path) in CommandLine.arguments.dropFirst().enumerated() {
 let url=URL(fileURLWithPath:path)
 do {
  let values=try url.resourceValues(forKeys:[.isDirectoryKey,.isSymbolicLinkKey,.isAliasFileKey,.volumeIsLocalKey,.volumeIsInternalKey,.volumeIsRemovableKey,.isUbiquitousItemKey])
  var st=stat();guard lstat(path,&st)==0 else{throw NSError(domain:"StorageProbe",code:1)}
  var syncMarker=false
  let length=listxattr(path,nil,0,XATTR_NOFOLLOW)
  if length>0 {
   var bytes=[CChar](repeating:0,count:length)
   if listxattr(path,&bytes,length,XATTR_NOFOLLOW)>=0 {
    let names=bytes.split(separator:0).map{String(decoding:$0.map{UInt8(bitPattern:$0)},as:UTF8.self).lowercased()}
    syncMarker=names.contains{n in ["cloud","ubiquity","fileprovider","dropbox","onedrive","syncthing"].contains{n.contains($0)}}
   }
  }
  checks.append([
   "component":index,
   "local_internal":values.volumeIsLocal==true && values.volumeIsInternal==true && values.volumeIsRemovable==false,
   "symlink_or_alias":values.isSymbolicLink==true || values.isAliasFile==true,
   "icloud_targeted":FileManager.default.isUbiquitousItem(at:url),
   "icloud_resource_attribute_available":values.isUbiquitousItem != nil,
   "placeholder":(st.st_flags & 0x40000000) != 0,
   "sync_marker":syncMarker,
   "directory":values.isDirectory==true
  ])
 } catch {checks.append(["component":index,"metadata_available":false])}
}
let result:[String:Any]=["schema":1,"checks":checks,"third_party_sync_requires_owner_confirmation":true]
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:result,options:.sortedKeys));print()
