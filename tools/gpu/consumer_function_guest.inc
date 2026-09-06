// Private accessors are checked on the actual receiver before use. Host method
// inventory is a rehearsal, not proof that this exact guest implements them.
@protocol DVMConstantAccess
- (NSArray *)newNamedConstantArray;
- (NSArray *)newIndexedConstantArray;
- (NSString *)name;
- (NSUInteger)index;
- (MTLDataType)dataType;
- (const void *)data;
@end
static void DVMRequireConstantABI(id object,SEL selector,const char *type) {
    NSMethodSignature *s=[object methodSignatureForSelector:selector];
    if(!s||s.numberOfArguments!=2||strcmp(s.methodReturnType,type))reject([NSString stringWithFormat:@"constant accessor ABI unsupported: %@ %@",NSStringFromClass([object class]),NSStringFromSelector(selector)]);
}
static NSArray *DVMConstants(MTLFunctionConstantValues *values) {
    if(!values)return @[];
    DVMRequireConstantABI(values,@selector(newNamedConstantArray),"@");
    DVMRequireConstantABI(values,@selector(newIndexedConstantArray),"@");
    NSMutableArray *result=[NSMutableArray array];
    NSArray *groups=@[[(id<DVMConstantAccess>)values newNamedConstantArray],[(id<DVMConstantAccess>)values newIndexedConstantArray]];
    for(unsigned group=0;group<2;group++){
        if(![groups[group] isKindOfClass:NSArray.class]||[groups[group] count]>128)reject(@"constant count");
        for(id<DVMConstantAccess> c in groups[group]){
            DVMRequireConstantABI(c,@selector(dataType),"Q");DVMRequireConstantABI(c,@selector(data),"^v");
            DVMRequireConstantABI(c,group?@selector(index):@selector(name),group?"Q":"@");
            NSUInteger size=DVMConstantBytes(c.dataType);if(!size||!c.data)reject(@"constant type/data unsupported");
            id key=group?@(c.index):c.name;
            [result addObject:@{@"key":key,@"indexed":@((BOOL)(group!=0)),@"type":@(c.dataType),@"data":[[NSData dataWithBytes:c.data length:size] base64EncodedStringWithOptions:0]}];
        }
    }
    return result;
}
