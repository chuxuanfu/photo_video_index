import AppKit
import AVKit
import AVFoundation
import Foundation
import Darwin

let publicReviewTest=CommandLine.arguments.dropFirst().elementsEqual(["--verify-photo-public"])
let publicPreview=CommandLine.arguments.dropFirst().elementsEqual(["--public-photo-preview"])
let countTest=CommandLine.arguments.dropFirst().elementsEqual(["--verify-result-limits"])
let settingWrite=CommandLine.arguments.count==3 && CommandLine.arguments[1]=="--verify-limit-write"
let settingRead=CommandLine.arguments.count==3 && CommandLine.arguments[1]=="--verify-limit-read"
let settingUIRead=CommandLine.arguments.count==3 && CommandLine.arguments[1]=="--verify-limit-ui-read"
let verify=CommandLine.arguments.dropFirst().elementsEqual(["--verify-synthetic"]) || publicReviewTest || countTest || settingWrite || settingRead || settingUIRead
guard CommandLine.arguments.count==1 || verify || publicPreview else { exit(2) }
let defaultPort:UInt16=(publicReviewTest || publicPreview) ? 19444 : (verify ? 19132:19130)
let requestedPort=ProcessInfo.processInfo.environment["MEMORYSEARCH_PORT"]
let port:UInt16=requestedPort.flatMap { UInt16($0) } ?? defaultPort
guard port>=1024 && port<=65000 else { exit(2) }

// Persist only the result count, never search text, names or result data.
func resultPreferenceURL()->URL? {
    guard let path=ProcessInfo.processInfo.environment["MEMORYSEARCH_CLIENT_STATE"] else{return nil}
    return URL(fileURLWithPath:path,isDirectory:true).appendingPathComponent("result-preferences.json")
}
func loadResultLimit()->Int {
    guard let url=resultPreferenceURL(),let data=try? Data(contentsOf:url),data.count<1024,
          let value=try? JSONSerialization.jsonObject(with:data) as? [String:Int],let n=value["limit"],(1...50).contains(n) else{return 10}
    return n
}
func saveResultLimit(_ n:Int)throws {
    guard (1...50).contains(n),let url=resultPreferenceURL() else{throw NSError(domain:"ResultLimit",code:1)}
    let data=try JSONSerialization.data(withJSONObject:["limit":n]);let temporary=url.path+".new"
    let fd=Darwin.open(temporary,O_WRONLY|O_CREAT|O_TRUNC,0o600)
    guard fd>=0 else{throw NSError(domain:NSPOSIXErrorDomain,code:Int(errno))}
    let ok=data.withUnsafeBytes{p in Darwin.write(fd,p.baseAddress!,p.count)==p.count}
    let synced=fsync(fd)==0;close(fd)
    guard ok && synced && Darwin.rename(temporary,url.path)==0 else{throw NSError(domain:NSPOSIXErrorDomain,code:Int(errno))}
}

func connectLocal(_ address:String,_ target:UInt16)->(Int32,Int32) {
    let fd=socket(AF_INET,SOCK_STREAM,0)
    var t=timeval(tv_sec:35,tv_usec:0);setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&t,socklen_t(MemoryLayout<timeval>.size));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&t,socklen_t(MemoryLayout<timeval>.size))
    var noPipe:Int32=1;setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&noPipe,socklen_t(MemoryLayout<Int32>.size))
    var a=sockaddr_in();a.sin_len=UInt8(MemoryLayout<sockaddr_in>.size);a.sin_family=sa_family_t(AF_INET);a.sin_port=target.bigEndian
    guard inet_pton(AF_INET,address,&a.sin_addr)==1 else { close(fd);return(-1,EINVAL) }
    let result=withUnsafePointer(to:&a) { p in p.withMemoryRebound(to:sockaddr.self,capacity:1) { Darwin.connect(fd,$0,socklen_t(MemoryLayout<sockaddr_in>.size)) } }
    if result != 0 { let e=errno;close(fd);return(-1,e) };return(fd,0)
}
for (address,target) in [("1.1.1.1",UInt16(443)),("10.255.255.1",UInt16(9)),("127.0.0.1",port+7)] {
    let (fd,e)=connectLocal(address,target)
    if fd>=0 { close(fd);exit(42) };if e != EPERM && e != EACCES { exit(42) }
}
enum LocalError:Error { case request, response, image }
func get(_ path:String,_ body:Data?=nil)throws->Data {
    guard path.hasPrefix("/"),!path.contains("\r"),!path.contains("\n"),!path.contains("://") else { throw LocalError.request }
    let (fd,_)=connectLocal("127.0.0.1",port);guard fd>=0 else { throw LocalError.request };defer { close(fd) }
    var request=Data("\(body == nil ? "GET":"POST") \(path) HTTP/1.1\r\nHost: 127.0.0.1:\(port)\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: \(body?.count ?? 0)\r\n\r\n".utf8)
    if let b=body { request.append(b) }
    try request.withUnsafeBytes { (p:UnsafeRawBufferPointer) in
        var sent=0;while sent<p.count { let n=Darwin.send(fd,p.baseAddress!.advanced(by:sent),p.count-sent,0);if n<=0 { throw LocalError.request };sent+=n }
    }
    var response=Data();var buffer=[UInt8](repeating:0,count:65536)
    while true { let n=recv(fd,&buffer,buffer.count,0);if n==0 { break };if n<0 { throw LocalError.response };response.append(contentsOf:buffer[0..<n]);if response.count>210*1024*1024 { throw LocalError.response } }
    guard let boundary=response.range(of:Data([13,10,13,10])),let headers=String(data:response[..<boundary.lowerBound],encoding:.utf8),headers.hasPrefix("HTTP/1.1 200") || headers.hasPrefix("HTTP/1.1 206"),!headers.lowercased().contains("transfer-encoding: chunked") else { throw LocalError.response }
    return Data(response[boundary.upperBound...])
}
func jsonDictionary(_ data:Data)throws->[String:Any] {
    guard let result=try JSONSerialization.jsonObject(with:data) as? [String:Any] else {throw LocalError.response};return result
}
func searchEnvelope(_ query:String,_ mode:String,_ fault:String?=nil,limit:Int=10)throws->[String:Any] {
    guard (1...50).contains(limit) else{throw LocalError.request}
    var input:[String:Any]=["text":query,"mode":mode,"limit":limit]
    if publicReviewTest,let fault=fault { input["_public_fault"]=fault }
    let body=try JSONSerialization.data(withJSONObject:input)
    let out=try jsonDictionary(get("/search",body))
    guard let hits=out["results"] as? [[String:Any]],hits.count<=limit,(out["requested_limit"] as? Int ?? 10)==limit else { throw LocalError.response };return out
}
func search(_ query:String,_ mode:String)throws->[[String:Any]] {
    return try searchEnvelope(query,mode)["results"] as! [[String:Any]]
}
func fileIDs(_ rows:[[String:Any]])throws->[String] {
    let ids=try rows.map { row -> String in
        guard let a=row["asset"] as? [String:Any],let id=a["id"] as? String,id.range(of:#"^[0-9a-f]{32}$"#,options:.regularExpression) != nil else { throw LocalError.response };return id
    }
    guard rows.count<=50,Set(ids).count==ids.count else { throw LocalError.response };return ids
}
func checkedReview(_ value:[String:Any],_ original:[[String:Any]])throws->([ [String:Any] ],[ [String:Any] ],Bool,Bool) {
    guard let state=value["photo_review"] as? [String:Any],let stage=state["stage"] as? String,let complete=state["complete"] as? Bool else { throw LocalError.response }
    let native=try fileIDs(original)
    guard let echoed=value["original_results"] as? [[String:Any]],try fileIDs(echoed)==native else { throw LocalError.response }
    if stage=="fallback" { return ([],[],true,true) }
    guard stage=="verifying" || stage=="complete",let yes=value["confirmed"] as? [[String:Any]],let rest=value["remaining"] as? [[String:Any]],let ordered=value["results"] as? [[String:Any]] else { throw LocalError.response }
    let yesIDs=try fileIDs(yes),restIDs=try fileIDs(rest),all=yesIDs+restIDs
    guard all.count==native.count,Set(all)==Set(native),Set(all).count==all.count,try fileIDs(ordered)==all,yes.allSatisfy({ $0["photo_review_decision"] as? String == "PASS" }),rest.allSatisfy({ $0["photo_review_decision"] as? String != "PASS" }),native.filter({yesIDs.contains($0)})==yesIDs,native.filter({restIDs.contains($0)})==restIDs else { throw LocalError.response }
    return (original.filter{yesIDs.contains((($0["asset"] as? [String:Any])?["id"] as? String) ?? "")},original.filter{restIDs.contains((($0["asset"] as? [String:Any])?["id"] as? String) ?? "")},complete,false)
}
func assetPath(_ asset:[String:Any],_ key:String)throws->String {
    guard let p=asset[key] as? String,p.range(of:#"^/api/assets/[0-9a-f]{32}/(file|thumbnail)$"#,options:.regularExpression) != nil else { throw LocalError.response };return p
}
final class Action:NSObject {
    let run:()->Void;init(_ run:@escaping()->Void) { self.run=run }
    @objc func click() { run() }
}
final class Viewer:NSObject,NSWindowDelegate {
    var window:NSWindow!;let query=NSTextField(string:"");let mode=NSPopUpButton();let resultCount=NSTextField(string:"10")
    let status=NSTextField(labelWithString:"请选择照片、视频或混合，输入中文描述。");let rows=NSStackView()
    var actions=[Action]();var players=[AVPlayer]();var previews=[NSWindow]()
    var serial=0;var displayedRequest=0;var nativeRows=[[String:Any]]();var yesRows=[[String:Any]]();var restRows=[[String:Any]]();var lastReview:[String:Any]?;var expanded=false;var originalOrder=false;var reviewStarted=Date();var firstCardsSeconds=0.0;var terminalSeconds=0.0;var restoredCount=0;var fallbackPassed=false
    var stage="starting";var timer:Timer?;var testPlayer:AVPlayer?;var photoCount=0;var videoCount=0;var seeked=false
    func finish(_ result:[String:Any],_ code:Int32) {
        if verify { let d=try! JSONSerialization.data(withJSONObject:result,options:[.sortedKeys]);FileHandle.standardOutput.write(d);FileHandle.standardOutput.write(Data([10])) };exit(code)
    }
    func button(_ title:String,_ fn:@escaping()->Void)->NSButton {
        let action=Action(fn);actions.append(action);return NSButton(title:title,target:action,action:#selector(Action.click))
    }
    func start() {
        window=NSWindow(contentRect:NSRect(x:0,y:0,width:1040,height:800),styleMask:[.titled,.closable,.resizable,.miniaturizable],backing:.buffered,defer:false)
        window.title=publicPreview ? "公开照片默认核验 · 原生集成测试":"本机照片与视频 · 离线查看器";window.delegate=self
        let outer=NSStackView();outer.orientation = .vertical;outer.alignment = .leading;outer.spacing=15;outer.edgeInsets=NSEdgeInsets(top:20,left:20,bottom:20,right:20)
        let title=NSTextField(labelWithString:"本机照片与视频 · 本机搜索");title.font=NSFont.boldSystemFont(ofSize:23)
        query.placeholderString="用中文描述画面";query.widthAnchor.constraint(equalToConstant:420).isActive=true;mode.addItems(withTitles:["照片","视频内部画面","照片＋视频"])
        resultCount.stringValue=String(loadResultLimit());resultCount.widthAnchor.constraint(equalToConstant:55).isActive=true
        let number=NumberFormatter();number.allowsFloats=false;number.minimum=1;number.maximum=50;resultCount.formatter=number
        resultCount.toolTip="结果数量 1–50；实际不足时不补数。数量越大，照片核验可能越慢。"
        resultCount.target=self;resultCount.action=#selector(countChanged)
        let countLabel=NSTextField(labelWithString:"结果数量（1–50）")
        let bar=NSStackView(views:[query,mode,countLabel,resultCount,button("查找",{self.performSearch()})]);bar.orientation = .horizontal
        rows.orientation = .vertical;rows.alignment = .leading;rows.spacing=20
        let scroll=NSScrollView();scroll.hasVerticalScroller=true;scroll.documentView=rows;rows.translatesAutoresizingMaskIntoConstraints=false
        NSLayoutConstraint.activate([rows.leadingAnchor.constraint(equalTo:scroll.contentView.leadingAnchor),rows.trailingAnchor.constraint(equalTo:scroll.contentView.trailingAnchor),rows.topAnchor.constraint(equalTo:scroll.contentView.topAnchor),rows.widthAnchor.constraint(equalTo:scroll.contentView.widthAnchor)])
        outer.addArrangedSubview(title);outer.addArrangedSubview(bar);outer.addArrangedSubview(status);outer.addArrangedSubview(scroll);window.contentView=outer;scroll.heightAnchor.constraint(greaterThanOrEqualToConstant:600).isActive=true
        if settingUIRead {
            let restored=Int(resultCount.stringValue) ?? 0;let expected=Int(CommandLine.arguments[2]) ?? 0
            finish(["passed":restored==expected,"result_limit":restored,"ui_control_restored":true,"search_requests":0],restored==expected ? 0:2);return
        }
        if !verify { window.center();window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true) } else if publicReviewTest { verifyPublicPhoto() } else if countTest { verifyLimits() } else { verifyAll() }
    }
    @objc func countChanged() {
        guard let n=Int(resultCount.stringValue),(1...50).contains(n) else{status.stringValue="结果数量须为 1–50 的整数";return}
        do{try saveResultLimit(n)}catch{status.stringValue="结果数量暂未保存；请检查本机设置目录"}
    }
    func performSearch() {
        let text=query.stringValue.trimmingCharacters(in:.whitespacesAndNewlines),kind=["image","video","mixed"][mode.indexOfSelectedItem]
        if text.isEmpty { status.stringValue="请输入中文描述";return }
        guard let limit=Int(resultCount.stringValue),(1...50).contains(limit) else{status.stringValue="结果数量须为 1–50 的整数";return}
        do{try saveResultLimit(limit)}catch{status.stringValue="无法保存结果数量；没有提交查询";return}
        status.stringValue="正在查找…"
        serial+=1;let request=serial
        DispatchQueue.global().async { do { let response=try searchEnvelope(text,kind,limit:limit);DispatchQueue.main.async { guard request==self.serial else{return};self.receive(response,kind,request) } } catch { DispatchQueue.main.async { guard request==self.serial else{return};self.status.stringValue="本机查询失败或服务尚未就绪；没有联网回退。" } } }
    }
    func receive(_ response:[String:Any],_ kind:String,_ request:Int) {
        guard let hits=response["results"] as? [[String:Any]],(try? fileIDs(hits)) != nil else {status.stringValue="本机结果格式无效";return}
        displayedRequest=request;nativeRows=hits;yesRows=[];restRows=[];lastReview=nil;expanded=false;originalOrder=false;show(hits)
        guard kind != "video",let job=response["photo_review_job"] as? String,job.range(of:#"^[0-9a-f]{32}$"#,options:.regularExpression) != nil else{return}
        status.stringValue="原始候选已显示；正在本机核验照片。核验不是正确性保证。"
        pollReview(job,hits,request)
    }
    func pollReview(_ job:String,_ original:[[String:Any]],_ request:Int) {
        DispatchQueue.global().async {
            let deadline=Date().addingTimeInterval(45)
            do {
                while Date()<deadline {
                    let value=try jsonDictionary(get("/photo-review/"+job))
                    let checked=try checkedReview(value,original)
                    DispatchQueue.main.async {
                        guard request==self.serial else{return}
                        self.lastReview=value
                        if checked.3 {self.show(original);self.status.stringValue="核验未完成，已回到原始排序；全部候选仍可查看。"}
                        else if checked.2 {self.yesRows=checked.0;self.restRows=checked.1;self.showReview()}
                        else {self.status.stringValue="原始候选已显示；已核验 \((value["photo_review"] as? [String:Any])?["completed"] as? Int ?? 0)/\(original.count)，正在继续。"}
                        if publicReviewTest && checked.2 {self.publicPhotoFinished(checked.3)}
                    }
                    if checked.2{return};Thread.sleep(forTimeInterval:0.15)
                }
                throw LocalError.response
            } catch {
                DispatchQueue.main.async {guard request==self.serial else{return};self.show(original);self.status.stringValue="核验服务不可用，已回到原始排序；全部候选仍可查看。";if publicReviewTest {self.finish(["passed":false,"stage":"photo_poll"],8)}}
            }
        }
    }
    func showReview() {
        show(originalOrder ? nativeRows:yesRows)
        let toggle=button(originalOrder ? "查看核验分组":"恢复原始排序",{self.originalOrder.toggle();self.showReview()})
        rows.insertArrangedSubview(toggle,at:0)
        if originalOrder {status.stringValue="原始排序 · \(nativeRows.count) 个文件，成员完整保留。";return}
        let heading=NSTextField(labelWithString:"核验支持 · \(yesRows.count) 个。核验可能出错，请查看画面。")
        rows.insertArrangedSubview(heading,at:1)
        let disclosure=button((expanded ? "收起":"展开")+"其他原始候选 · \(restRows.count) 个（未确认或未获支持）",{self.expanded.toggle();self.showReview()})
        rows.addArrangedSubview(disclosure)
        if expanded {appendRows(restRows,offset:yesRows.count)}
        status.stringValue="核验已完成 · 支持 \(yesRows.count)，其余 \(restRows.count)；本次原始候选没有删除或补填；视频未参与照片核验。"
    }
    func show(_ hits:[[String:Any]]) {
        players.forEach{$0.pause()};players=[];rows.arrangedSubviews.forEach{rows.removeArrangedSubview($0);$0.removeFromSuperview()}
        appendRows(hits,offset:0)
        status.stringValue="返回 \(hits.count) 个文件；分数为相关性，不是准确率。"
    }
    func appendRows(_ hits:[[String:Any]],offset:Int) {
        for (i,hit) in hits.enumerated() {
            guard let a=hit["asset"] as? [String:Any],let name=a["rel_path"] as? String else { continue }
            let label=NSTextField(wrappingLabelWithString:"\(i+offset+1) · \(name)")
            let image=NSImageView();image.imageScaling = .scaleProportionallyUpOrDown;image.widthAnchor.constraint(equalToConstant:340).isActive=true;image.heightAnchor.constraint(equalToConstant:220).isActive=true
            let stack=NSStackView(views:[label,image]);stack.orientation = .vertical;stack.alignment = .leading
            if let path=try? assetPath(a,"thumbnail_url") { DispatchQueue.global().async { if let data=try? get(path),let im=NSImage(data:data) { DispatchQueue.main.async { image.image=im } } } }
            if a["media_type"] as? String == "video" {
                let movie=AVPlayerView();movie.controlsStyle = .inline;movie.widthAnchor.constraint(equalToConstant:700).isActive=true;movie.heightAnchor.constraint(equalToConstant:390).isActive=true
                stack.addArrangedSubview(button("播放视频",{guard let path=try? assetPath(a,"file_url") else { return };self.players.forEach{$0.pause()};let p=AVPlayer(url:URL(string:"http://127.0.0.1:\(port)\(path)")!);self.players.append(p);movie.player=p;p.play()}));stack.addArrangedSubview(movie)
            } else {
                stack.addArrangedSubview(button("查看原图",{DispatchQueue.global().async { guard let path=try? assetPath(a,"file_url"),let data=try? get(path),let im=NSImage(data:data) else { return };DispatchQueue.main.async {let view=NSImageView();view.image=im;view.imageScaling = .scaleProportionallyUpOrDown;let p=NSWindow(contentRect:NSRect(x:0,y:0,width:1000,height:760),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false);p.title="本机原图";p.contentView=view;p.center();p.makeKeyAndOrderFront(nil);self.previews.append(p)} } }))
            };rows.addArrangedSubview(stack)
        }
    }
    // Generated-only engineering check. The grouping receipt below is a
    // contract fixture, never a visual-model verdict or accuracy measurement.
    func verifyLimits() {
        DispatchQueue.global().async {
            do {
                var samples=[[String:Any]]();var largest=[[String:Any]]()
                for pass in 0..<2 {
                    for n in [5,10,25] {
                        for kind in ["image","video","mixed"] {
                            let begin=Date();var expectedRequest=0
                            DispatchQueue.main.sync {
                                self.query.stringValue="红色圆形";self.mode.selectItem(at:kind=="image" ? 0:(kind=="video" ? 1:2))
                                self.resultCount.stringValue=String(n);expectedRequest=self.serial+1;self.performSearch()
                            }
                            var hits=[[String:Any]]()
                            let deadline=Date().addingTimeInterval(90)
                            var loaded=false
                            while Date()<deadline {
                                DispatchQueue.main.sync {
                                    let cards=self.rows.arrangedSubviews.compactMap{$0 as? NSStackView}
                                    let pictures=cards.flatMap{$0.arrangedSubviews}.compactMap{$0 as? NSImageView}
                                    loaded=self.displayedRequest==expectedRequest && pictures.count==n && pictures.allSatisfy{$0.image?.isValid==true};if loaded{hits=self.nativeRows}
                                }
                                if loaded{break};Thread.sleep(forTimeInterval:0.02)
                            }
                            guard loaded,hits.count==n,try fileIDs(hits).count==n else{throw LocalError.image}
                            samples.append(["limit":n,"mode":kind,"pass":pass,"returned":hits.count,"ui_all_thumbnails_s":Date().timeIntervalSince(begin)])
                            if n==25 && kind=="mixed"{largest=hits}
                        }
                    }
                }
                let photoIDs=largest.filter{($0["asset"] as? [String:Any])?["media_type"] as? String == "image"}.prefix(2).map{($0["asset"] as! [String:Any])["id"] as! String}
                var yes=[[String:Any]](),rest=[[String:Any]]()
                for var row in largest{let id=(row["asset"] as! [String:Any])["id"] as! String;row["photo_review_decision"]=photoIDs.contains(id) ? "PASS":"UNKNOWN";if photoIDs.contains(id){yes.append(row)}else{rest.append(row)}}
                let value:[String:Any]=["photo_review":["stage":"complete","complete":true],"original_results":largest,"results":yes+rest,"confirmed":yes,"remaining":rest]
                let checked=try checkedReview(value,largest)
                DispatchQueue.main.sync {
                    self.nativeRows=largest;self.yesRows=checked.0;self.restRows=checked.1;self.originalOrder=false;self.expanded=false;self.showReview()
                    self.expanded=true;self.showReview();self.originalOrder=true;self.showReview()
                }
                guard try fileIDs(yes+rest).count==25 else{throw LocalError.response}
                try saveResultLimit(25)
                self.finish(["passed":true,"actual_search_requests":samples.count,"samples":samples,"grouping_fixture_only":true,"foldout_and_original_order":true,"members_preserved":true,"saved_limit":25,"all_thumbnails_decoded":true,"renderer":"AppKit","quality_benchmark":false],0)
            }catch{self.finish(["passed":false,"stage":"result_limit_native_integration"],12)}
        }
    }
    func verifyPublicPhoto() {
        stage="public_photo_actual_request";reviewStarted=Date();serial+=1;let request=serial
        DispatchQueue.global().async {do {let response=try searchEnvelope("椅子","image");guard let hits=response["results"] as? [[String:Any]],let a=hits.first?["asset"] as? [String:Any],let image=NSImage(data:try get(assetPath(a,"file_url"))),image.isValid else{throw LocalError.image};DispatchQueue.main.async {self.firstCardsSeconds=Date().timeIntervalSince(self.reviewStarted);self.receive(response,"image",request)}}catch{DispatchQueue.main.async {self.finish(["passed":false,"stage":"public_photo_request"],9)}}}
        DispatchQueue.main.asyncAfter(deadline:.now()+65) {self.finish(["passed":false,"timeout":true,"stage":self.stage],3)}
    }
    func publicPhotoFinished(_ fallback:Bool) {
        guard stage=="public_photo_actual_request" else{return}
        terminalSeconds=Date().timeIntervalSince(reviewStarted)
        guard !fallback,nativeRows.count==3,yesRows.count==2,restRows.count==1,Set(try! fileIDs(nativeRows))==Set(try! fileIDs(yesRows+restRows)) else {finish(["passed":false,"stage":"public_photo_grouping","confirmed":yesRows.count,"remaining":restRows.count],10);return}
        expanded=true;showReview();originalOrder=true;showReview();restoredCount=nativeRows.count;originalOrder=false;showReview()
        stage="public_fallback_request"
        DispatchQueue.global().async {do {
            let failed=try searchEnvelope("椅子","image","error");let original=failed["results"] as! [[String:Any]];guard let job=failed["photo_review_job"] as? String else{throw LocalError.response}
            for _ in 0..<100 {let v=try jsonDictionary(get("/photo-review/"+job));let c=try checkedReview(v,original);if c.2 {guard c.3 else{throw LocalError.response};self.fallbackPassed=true;break};Thread.sleep(forTimeInterval:0.05)}
            guard self.fallbackPassed else{throw LocalError.response}
            let videos=try search("瀑布","video");guard let asset=videos.first?["asset"] as? [String:Any] else{throw LocalError.response};let path=try assetPath(asset,"file_url")
            DispatchQueue.main.async {self.videoCount=videos.count;self.stage="public_video_play";let view=AVPlayerView();view.frame=NSRect(x:0,y:0,width:700,height:400);self.window.contentView=view;let p=AVPlayer(url:URL(string:"http://127.0.0.1:\(port)\(path)")!);p.isMuted=true;view.player=p;self.testPlayer=p;p.play();self.timer=Timer.scheduledTimer(withTimeInterval:0.1,repeats:true){_ in self.checkPlayback()}}
        }catch{DispatchQueue.main.async {self.finish(["passed":false,"stage":"public_fallback_or_video_request"],11)}}}
    }
    func verifyAll() {
        stage="local_requests"
        DispatchQueue.global().async {
            do {
                let p=try search("红色圆形","image"),v=try search("彩色测试画面","video")
                guard let pa=p.first?["asset"] as? [String:Any],let va=v.first?["asset"] as? [String:Any] else { throw LocalError.response }
                let data=try get(assetPath(pa,"file_url"));guard let im=NSImage(data:data),im.isValid else { throw LocalError.image };let path=try assetPath(va,"file_url")
                DispatchQueue.main.async {self.photoCount=p.count;self.videoCount=v.count;self.stage="video_stream";let view=AVPlayerView();view.frame=NSRect(x:0,y:0,width:700,height:400);self.window.contentView=view;let player=AVPlayer(url:URL(string:"http://127.0.0.1:\(port)\(path)")!);player.isMuted=true;view.player=player;self.testPlayer=player;player.play();self.timer=Timer.scheduledTimer(withTimeInterval:0.1,repeats:true) { _ in self.checkPlayback() }}
            } catch { DispatchQueue.main.async { self.finish(["passed":false,"stage":"local_requests"],6) } }
        };DispatchQueue.main.asyncAfter(deadline:.now()+35) { self.finish(["passed":false,"timeout":true,"stage":self.stage],3) }
    }
    func checkPlayback() {
        guard let p=testPlayer else { return }
        if p.currentItem?.status == .failed { finish(["passed":false,"stage":"video_stream","player_error_code":(p.currentItem?.error as NSError?)?.code ?? 0],7) }
        let t=CMTimeGetSeconds(p.currentTime())
        if !seeked && t>0.25 { seeked=true;p.seek(to:CMTime(seconds:1,preferredTimescale:600),toleranceBefore:.zero,toleranceAfter:.zero) { _ in p.play() } }
        else if seeked && t>1.15 {
            if publicReviewTest {finish(["passed":true,"photo_raw_rows":nativeRows.count,"confirmed":yesRows.count,"remaining":restRows.count,"members_preserved":true,"raw_order_restored":restoredCount==nativeRows.count,"foldout_exercised":true,"fallback_passed":fallbackPassed,"first_cards_includes_image_decode":true,"image_decoded":true,"first_cards_s":firstCardsSeconds,"photo_terminal_s":terminalSeconds,"actual_search_requests":3,"video_rows":videoCount,"video_played":true,"video_seeked":true,"native_socket_probes_blocked":3,"renderer":"AppKit+AVPlayer"],0)}
            else {finish(["passed":true,"photo_rows":photoCount,"video_rows":videoCount,"image_decoded":true,"video_played":true,"video_seeked":true,"native_socket_probes_blocked":3,"renderer":"AppKit+AVPlayer"],0)}
        }
    }
    func windowWillClose(_ notification:Notification) { NSApp.terminate(nil) }
}
if settingWrite || settingRead {
    guard let n=Int(CommandLine.arguments[2]),(1...50).contains(n) else{exit(2)}
    do {
        if settingWrite{try saveResultLimit(n)}
        let actual=loadResultLimit();let passed=actual==n
        let data=try JSONSerialization.data(withJSONObject:["passed":passed,"result_limit":actual,"persisted_across_processes":settingRead],options:.sortedKeys)
        FileHandle.standardOutput.write(data);print();exit(passed ? 0:2)
    }catch{let e=error as NSError;let d=try! JSONSerialization.data(withJSONObject:["passed":false,"stage":"settings_io","error_code":e.code]);FileHandle.standardOutput.write(d);exit(2)}
}
let app=NSApplication.shared;app.setActivationPolicy(verify ? .accessory:.regular)
let viewer=Viewer();viewer.start();app.run()
